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
translation_unit_main_file = None # Translation unit name
translation_unit_files = []  # Store the mapping from line number to file info

# Parse the .i file to extract #line information and track it
def parse_i_file_for_line_info(i_file):
    processed_files = set()  # Set to track file IDs

    global translation_unit_files
    global translation_unit_main_file

    with open(i_file, 'r') as f:
        lines = f.readlines()

    for i, line in enumerate(lines, start=1):
        match = re.match(r'#line (\d+) "(.*)"', line)
        if not match:
            continue
            
        line_number = int(match.group(1))
        file_path = os.path.relpath(match.group(2), opencv_source_folder)

        #if "Program Files" in file_path:
        #    continue

        translation_unit_files.append((i, line_number, file_path))
        if translation_unit_main_file is None:
            translation_unit_main_file = get_id(file_path)
        
        # Add the file vertex with its ID
        if file_path not in processed_files:
            add_vertex_to_gremlin('File', {"name": os.path.basename(file_path), "path": file_path}, file_path)
            processed_files.add(file_path)

last_checked_index = 0  # Initialize the global variable

# Get the file and line number for a given cursor
def get_file_and_line(cursor):
    cursor_line = cursor.location.line
    global last_checked_index
    file_id = None
    file_line = None

    for i in range(last_checked_index, len(translation_unit_files)):
        directive_line, original_line, file_id = translation_unit_files[i]
        if cursor_line >= directive_line:
            line_offset = cursor_line - directive_line
            file_line = original_line + line_offset
            last_checked_index = i 
        else:
            break  # Stop when the cursor_line no longer matches or is less than directive_line

    return file_id, file_line

# Get id of a cursor
def get_id(cursor_or_file_path):
    if isinstance(cursor_or_file_path, clang.cindex.Cursor):
        usr = cursor_or_file_path.get_usr()
        if usr:
            usr = usr.replace('#', '_')
        return usr
    return cursor_or_file_path.replace(os.path.sep, '_')

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
    properties["module"] = translation_unit_main_file

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

# Extract access specifier
def extract_access_specifier(cursor):
    access_specifier_map = {
        clang.cindex.AccessSpecifier.PUBLIC: "public",
        clang.cindex.AccessSpecifier.PRIVATE: "private",
        clang.cindex.AccessSpecifier.PROTECTED: "protected"
    }
    return access_specifier_map.get(cursor.access_specifier, "private")

# Process namespace
def process_namespace(cursor):
    namespace_info = {
        "name": cursor.spelling if cursor.spelling else f"anonymous_{get_id(cursor)}",
    }

    add_vertex_to_gremlin('Namespace', namespace_info, cursor)

METHOD_KINDS = {CursorKind.CXX_METHOD, CursorKind.CONSTRUCTOR, CursorKind.DESTRUCTOR, CursorKind.CONVERSION_FUNCTION}

# Process class or struct
def process_class_or_struct(cursor):
    kind = "class" if cursor.kind == CursorKind.CLASS_DECL else "struct"

    class_info = {
        "name": cursor.spelling,
        "kind": kind,
        #"access_specifier": extract_access_specifier(cursor),
        #"is_definition": cursor.is_definition()
    }

    add_vertex_to_gremlin(kind.capitalize(), class_info, cursor)

    # Add inheritance edges
    for base in cursor.get_children():
        if base.kind == CursorKind.CXX_BASE_SPECIFIER:
            base_class_cursor = base.type.get_declaration()
            access_specifier = extract_access_specifier(base)
            # Add the 'inherits' edge with the access_specifier property
            add_edge_to_gremlin(cursor, 'inherits', base_class_cursor, 'access_specifier', access_specifier)

    # Process methods, including constructors and conversion functions, and member variables
    for child in cursor.get_children():        
        if child.kind in METHOD_KINDS:
            process_function(child)
            add_edge_to_gremlin(cursor, 'contains', child)
        elif child.kind == CursorKind.FIELD_DECL:
            variable_info = {
                "name": child.spelling,
                "type": child.type.spelling,
                "access_specifier": extract_access_specifier(child)
            }

            # Add the variable vertex with custom ID
            add_vertex_to_gremlin('Variable', variable_info, child)
            add_edge_to_gremlin(cursor, 'contains', child)
        # TODO: add inner classes/structs here?

def process_union(cursor):
    union_info = {
        "name": cursor.spelling,
        "kind": "union"
    }

    add_vertex_to_gremlin('Union', union_info, cursor)

    # Process fields within the union
    for child in cursor.get_children():
        if child.kind == CursorKind.FIELD_DECL:
            field_info = {
                "name": child.spelling,
                "type": child.type.spelling, # TODO: or reference to a type?
            }
            add_vertex_to_gremlin('Field', field_info, child)
            add_edge_to_gremlin(cursor, 'contains', child)

# Process function
def process_function(cursor):
    function_info = {
        "name": cursor.spelling,        
        "return_type": cursor.result_type.spelling, # TODO: reference to actual type? (can be clan.cindex.Type)
    }

    add_vertex_to_gremlin('Function', function_info, cursor)

# Process enum
def process_enum(cursor):
    enum_info = {
        "name": cursor.spelling,
    }

    add_vertex_to_gremlin('Enum', enum_info, cursor)

    for child in cursor.get_children():
        if child.kind == CursorKind.ENUM_CONSTANT_DECL:
            enum_value_info = {
                "name": child.spelling,
            }
            add_vertex_to_gremlin('EnumValue', enum_value_info, child)
            add_edge_to_gremlin(cursor, 'contains', child)

PARENT_KINDS = {CursorKind.NAMESPACE, CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL}

# Traverse the AST
def process_cursor(cursor):
    file_id, file_line = get_file_and_line(cursor)
    if file_id is None:
        return

    processed = True
    
    if cursor.kind == CursorKind.NAMESPACE:
        if not vertex_exists(cursor):
            process_namespace(cursor)
    else:      
        if vertex_exists(cursor):
            return

        if cursor.kind == CursorKind.CLASS_DECL or cursor.kind == CursorKind.STRUCT_DECL:
            process_class_or_struct(cursor)
        elif cursor.kind == CursorKind.UNION_DECL:
            process_union(cursor)
        elif cursor.kind == CursorKind.FUNCTION_DECL:
            process_function(cursor)
        elif cursor.kind == CursorKind.ENUM_DECL:
            process_enum(cursor)
        else:
            processed = False

        if processed and cursor.semantic_parent and cursor.semantic_parent.kind in PARENT_KINDS:
            add_edge_to_gremlin(cursor.semantic_parent, 'contains', cursor)

    if processed:
        add_edge_to_gremlin(file_id, 'contains', cursor, 'line', file_line)

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