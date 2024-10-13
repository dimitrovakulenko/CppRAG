import clang.cindex
from clang.cindex import CursorKind
from gremlin_python.driver import client, serializer
from dotenv import load_dotenv
import os
import re

# Load environment variables
load_dotenv()

opencv_source_folder = "C:\\Users\\vakul\\source\\repos\\opencv\\"
opencv_build_folder = "C:/Users/vakul/source/repos/opencv/build/"
opencv_build_module_to_process = opencv_build_folder + "modules/core/opencv_core.dir/Debug/"
file_to_process = opencv_build_module_to_process + "algorithm.i"

# Set up clang index library
clang.cindex.Config.set_library_file('C:\\Program Files\\LLVM\\bin\\libclang.dll')

# Set up Gremlin client
gremlin_client = client.Client('wss://example-opencv.gremlin.cosmos.azure.com:443/', 'g',
                               username="/dbs/codebase/colls/codebase-graph",
                               password=os.getenv("COSMOS_DB_PRIMARY_KEY"),
                               message_serializer=serializer.GraphSONSerializersV2d0())

# Track the current file being parsed
current_file = None
file_lines_mapping = []  # Store the mapping from line number to file info

# Parse the .i file to extract #line information and track it
def parse_i_file_for_line_info(i_file):
    global file_lines_mapping
    global module_name

    with open(i_file, 'r') as f:
        lines = f.readlines()

    for i, line in enumerate(lines, start=1):
        match = re.match(r'#line (\d+) "(.*)"', line)
        if match:
            line_number = int(match.group(1))
            file_path = os.path.relpath(match.group(2), opencv_source_folder)
            file_lines_mapping.append((i, line_number, file_path))

    module_name = file_lines_mapping[0][2].replace(os.path.sep, '_')

# Get the actual file and line number for a given cursor
def get_actual_file_and_line(cursor_line):
    actual_file = None
    actual_line = None

    for line_info in reversed(file_lines_mapping):
        directive_line, original_line, file_path = line_info
        if cursor_line >= directive_line:
            line_offset = cursor_line - directive_line
            actual_file = file_path
            actual_line = original_line + line_offset
            break

    if actual_file is not None and "Program Files" in actual_file:
        return None, None

    return actual_file, actual_line

def vertex_exists(cursor):
    query = "g.V().has('id', id).count()"
    bindings = {"id": get_id(cursor)}
    
    # Execute the query and get the result
    result = gremlin_client.submit(query, bindings).all().result()
    
    # Return True if the count is greater than 0, meaning the vertex exists
    return result[0] > 0

# Add vertex to Gremlin database
def add_vertex_to_gremlin(label, properties, cursor):
    properties["id"] = get_id(cursor)
    properties["module"] = module_name

    properties_pairs = [(key, value) for key, value in properties.items()]
    query = "g.addV(label)"
    bindings = {"label": label}

    for i, (key, value) in enumerate(properties_pairs):
        query += f".property('{key}', key_{i})"
        bindings[f"key_{i}"] = value

    gremlin_client.submit(query, bindings)

# Add edge to Gremlin database
def add_edge_to_gremlin(from_cursor, edge_label, to_cursor, property_key=None, property_value=None):
    # Start the base query
    query = ("g.V(from_id).as('from')"
             ".V(to_id).addE(edge_label)")
    bindings = {
        "from_id": get_id(from_cursor),
        "to_id": get_id(to_cursor),
        "edge_label": edge_label
    }

    # Add property if provided
    if property_key and property_value:
        query += ".property(edge_property_key, edge_property_value)"
        bindings["edge_property_key"] = property_key
        bindings["edge_property_value"] = property_value

    # Finalize the query
    query += ".from('from')"

    gremlin_client.submit(query, bindings)

# Get id of a cursor
def get_id(cursor):
    usr = cursor.get_usr()
    if usr:
        usr = usr.replace('#', '_')
    return usr

# Extract namespace and classes
def extract_namespace_and_classes(cursor):
    actual_file = None
    entities = []
    current = cursor.semantic_parent

    # Walk up the hierarchy, extracting namespaces and class/struct names
    while current is not None:
        if current.kind == CursorKind.NAMESPACE:
            if current.spelling:  # Normal namespace
                entities.insert(0, current.spelling)
            else:  # Anonymous namespace
                if not actual_file:
                    actual_file, _ = get_actual_file_and_line(cursor.location.line)
                entities.insert(0, f"anonymous_{actual_file.replace(os.path.sep, '_')}")
        elif current.kind in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL, CursorKind.UNION_DECL):
            # Add class/struct/union names
            entities.insert(0, current.spelling)
        
        current = current.semantic_parent

    return "::".join(entities)

# Extract parameters
def extract_parameters(cursor):
    parameters = []
    for arg in cursor.get_arguments():
        param_name = arg.spelling
        param_type = arg.type.spelling
        parameters.append({"type": param_type, "name": param_name})
    return parameters

# Extract access specifier
def extract_access_specifier(cursor):
    access_specifier_map = {
        clang.cindex.AccessSpecifier.PUBLIC: "public",
        clang.cindex.AccessSpecifier.PRIVATE: "private",
        clang.cindex.AccessSpecifier.PROTECTED: "protected"
    }
    return access_specifier_map.get(cursor.access_specifier, "private")

# Process class or struct
def process_class_or_struct(cursor, actual_file, actual_line):
    kind = "class" if cursor.kind == CursorKind.CLASS_DECL else "struct"

    class_info = {
        "name": cursor.spelling,
        "kind": kind,
        "access_specifier": extract_access_specifier(cursor),
        "namespace": extract_namespace_and_classes(cursor),
        "file": actual_file,
        "line_number": actual_line,
        "is_definition": cursor.is_definition()
    }

    add_vertex_to_gremlin(kind.capitalize(), class_info, cursor)

    # Add inheritance edges
    for base in cursor.get_children():
        if base.kind == CursorKind.CXX_BASE_SPECIFIER:
            base_class_cursor = base.type.get_declaration()

            # Check for the access specifier
            access_specifier_map = {
                clang.cindex.AccessSpecifier.PUBLIC: 'public',
                clang.cindex.AccessSpecifier.PROTECTED: 'protected',
                clang.cindex.AccessSpecifier.PRIVATE: 'private'
            }
            access_specifier = access_specifier_map.get(base.access_specifier, 'private')

            # Add the 'inherits' edge with the access_specifier property
            add_edge_to_gremlin(cursor, 'inherits', base_class_cursor, 'access_specifier', access_specifier)


    METHOD_KINDS = {CursorKind.CXX_METHOD, CursorKind.CONSTRUCTOR, CursorKind.DESTRUCTOR, CursorKind.CONVERSION_FUNCTION}

    # Process methods, including constructors and conversion functions, and member variables
    for child in cursor.get_children():
        actual_file, actual_line = get_actual_file_and_line(child.location.line)  # Optimized with already known file
        
        if child.kind in METHOD_KINDS:
            process_function(child, actual_file, actual_line, cursor)
        elif child.kind == CursorKind.FIELD_DECL:
            variable_info = {
                "name": child.spelling,
                "type": child.type.spelling,
                "access_specifier": extract_access_specifier(child),
                "file": actual_file,
                "line_number": actual_line
            }

            # Add the variable vertex with custom ID
            add_vertex_to_gremlin('Variable', variable_info, child)
            add_edge_to_gremlin(cursor, 'contains', child)

def process_union(cursor, actual_file, actual_line):
    union_info = {
        "name": cursor.spelling,
        "kind": "union",
        "namespace": extract_namespace_and_classes(cursor),
        "file": actual_file,
        "line_number": actual_line
    }

    add_vertex_to_gremlin('Union', union_info, cursor)

    # Process fields within the union
    for child in cursor.get_children():
        if child.kind == CursorKind.FIELD_DECL:
            field_info = {
                "name": child.spelling,
                "type": child.type.spelling,
                "file": actual_file,
                "line_number": child.location.line
            }
            add_vertex_to_gremlin('Field', field_info, child)
            add_edge_to_gremlin(cursor, 'contains', child)

# Process function
def process_function(cursor, actual_file, actual_line, class_cursor=None):
    function_info = {
        "name": cursor.spelling,
        "return_type": cursor.result_type.spelling,
        "namespace": extract_namespace_and_classes(cursor),
        "file": actual_file,
        "line_number": actual_line,
    }

    add_vertex_to_gremlin('Function', function_info, cursor)

    if class_cursor:
        add_edge_to_gremlin(class_cursor, 'contains', cursor)

# Process enum
def process_enum(cursor, actual_file, actual_line):
    enum_info = {
        "name": cursor.spelling,
        "namespace": extract_namespace_and_classes(cursor),
        "file": actual_file,
        "line_number": actual_line
    }

    add_vertex_to_gremlin('Enum', enum_info, cursor)

    for child in cursor.get_children():
        if child.kind == CursorKind.ENUM_CONSTANT_DECL:
            actual_file, actual_line = get_actual_file_and_line(child.location.line) #TODO: optimize, file is already known

            enum_value_info = {
                "name": child.spelling,
                "enum": cursor.spelling,
                "file": actual_file,
                "line_number": actual_line
            }
            add_vertex_to_gremlin('EnumValue', enum_value_info, child)
            add_edge_to_gremlin(cursor, 'contains', child)

# Traverse the AST
def process_cursor(cursor):
    if vertex_exists(cursor):
        return
    actual_file, actual_line = get_actual_file_and_line(cursor.location.line)
    if not actual_file:
        return

    if cursor.kind == CursorKind.CLASS_DECL or cursor.kind == CursorKind.STRUCT_DECL:
        process_class_or_struct(cursor, actual_file, actual_line)
    elif cursor.kind == CursorKind.UNION_DECL:
        process_union(cursor, actual_file, actual_line)
    elif cursor.kind == CursorKind.FUNCTION_DECL:
        process_function(cursor, actual_file, actual_line)
    elif cursor.kind == CursorKind.ENUM_DECL:
        process_enum(cursor, actual_file, actual_line)

    for child in cursor.get_children():
        process_cursor(child)

# Main script to parse .i file
parse_i_file_for_line_info(file_to_process)
index = clang.cindex.Index.create()
translation_unit = index.parse(file_to_process, args=['-x', 'c++', '-std=c++11',
                                                      '-fparse-all-comments',
                                                      '-fno-delayed-template-parsing',
                                                      '-ferror-limit=0'])
for child in translation_unit.cursor.get_children():
    process_cursor(child)