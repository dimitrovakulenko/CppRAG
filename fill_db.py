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

    with open(i_file, 'r') as f:
        lines = f.readlines()

    for i, line in enumerate(lines, start=1):
        match = re.match(r'#line (\d+) "(.*)"', line)
        if match:
            line_number = int(match.group(1))
            file_path = os.path.relpath(match.group(2), opencv_source_folder)
            file_lines_mapping.append((i, line_number, file_path))

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

    if "Program Files" in actual_file:
        return None, None

    return actual_file, actual_line

# Add vertex to Gremlin database
def add_vertex_to_gremlin(label, properties, custom_id):
    if custom_id:
        properties["id"] = custom_id  # Set custom ID if provided
    properties["module"] = "onePartition"  # Ensure partition key is included

    properties_pairs = [(key, value) for key, value in properties.items()]
    query = "g.addV(label)"
    bindings = {"label": label}

    for i, (key, value) in enumerate(properties_pairs):
        query += f".property('{key}', key_{i})"
        bindings[f"key_{i}"] = value

    gremlin_client.submitAsync(query, bindings)

# Add edge to Gremlin database
def add_edge_to_gremlin(from_label, from_name, edge_label, to_label, to_name):
    query = ("g.V().has(from_label, 'name', from_name).as('from')"
             ".V().has(to_label, 'name', to_name).addE(edge_label).from('from')")
    bindings = {
        "from_label": from_label,
        "from_name": from_name,
        "to_label": to_label,
        "to_name": to_name,
        "edge_label": edge_label
    }
    gremlin_client.submitAsync(query, bindings)

# Extract namespace
def extract_namespace(cursor):
    namespace = []
    current = cursor.semantic_parent
    while current is not None and current.kind == CursorKind.NAMESPACE:
        namespace.insert(0, current.spelling)
        current = current.semantic_parent
    return "::".join(namespace)

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
    custom_id = f"{extract_namespace(cursor)}_{cursor.spelling}__{actual_file}".replace(' ', '_').replace(os.path.sep, '_')

    class_info = {
        "name": cursor.spelling,
        "kind": kind,
        "access_specifier": extract_access_specifier(cursor),
        "namespace": extract_namespace(cursor),
        "file": actual_file,
        "line_number": actual_line,
        "is_definition": cursor.is_definition()
    }

    add_vertex_to_gremlin(kind.capitalize(), class_info, custom_id)

    # Add inheritance edges
    for base in cursor.get_children():
        if base.kind == CursorKind.CXX_BASE_SPECIFIER:
            base_name = base.type.spelling
            add_edge_to_gremlin(kind.capitalize(), cursor.spelling, 'inherits', kind.capitalize(), base_name)

    # Process methods and member variables
    for child in cursor.get_children():
        actual_file, actual_line = get_actual_file_and_line(child.location.line) #TODO: optimize, file is already known

        if child.kind == CursorKind.CXX_METHOD:
            process_function(child, actual_file, actual_line, class_name=cursor.spelling, is_member=True)
        elif child.kind == CursorKind.FIELD_DECL:
            variable_info = {
                "name": child.spelling,
                "type": child.type.spelling,
                "access_specifier": extract_access_specifier(child),
                "file": actual_file,
                "line_number": actual_line
            }

            # Generate a custom ID for the variable
            custom_id = f"{extract_namespace(cursor)}_{child.spelling}__{actual_file}".replace(' ', '_').replace(os.path.sep, '_')

            # Add the variable vertex with custom ID
            add_vertex_to_gremlin('Variable', variable_info, custom_id)
            add_edge_to_gremlin(kind.capitalize(), cursor.spelling, 'contains', 'Variable', child.spelling)

# Process function
def process_function(cursor, actual_file, actual_line, class_name=None, is_member=False):
    custom_id = f"{extract_namespace(cursor)}_{cursor.spelling}__{actual_file}".replace(' ', '_').replace(os.path.sep, '_')

    function_info = {
        "name": cursor.spelling,
        "return_type": cursor.result_type.spelling,
        "namespace": extract_namespace(cursor),
        "file": actual_file,
        "line_number": actual_line,
        "is_member": is_member
    }

    add_vertex_to_gremlin('Function', function_info, custom_id)

    if class_name:
        add_edge_to_gremlin('Class', class_name, 'contains', 'Function', cursor.spelling)

# Process enum
def process_enum(cursor, actual_file, actual_line):
    custom_id = f"{extract_namespace(cursor)}_{cursor.spelling}__{actual_file}".replace(' ', '_').replace(os.path.sep, '_')

    enum_info = {
        "name": cursor.spelling,
        "namespace": extract_namespace(cursor),
        "file": actual_file,
        "line_number": actual_line
    }

    add_vertex_to_gremlin('Enum', enum_info, custom_id)

    for child in cursor.get_children():
        if child.kind == CursorKind.ENUM_CONSTANT_DECL:
            actual_file, actual_line = get_actual_file_and_line(child.location.line) #TODO: optimize, file is already known

            enum_value_id = f"{extract_namespace(cursor)}_{child.spelling}__{actual_file}".replace(' ', '_').replace(os.path.sep, '_')
            enum_value_info = {
                "name": child.spelling,
                "enum": cursor.spelling,
                "file": actual_file,
                "line_number": actual_line
            }
            add_vertex_to_gremlin('EnumValue', enum_value_info, enum_value_id)
            add_edge_to_gremlin('Enum', cursor.spelling, 'contains', 'EnumValue', child.spelling)

# Traverse the AST
def process_cursor(cursor):
    actual_file, actual_line = get_actual_file_and_line(cursor.location.line)
    if not actual_file:
        return

    if cursor.kind == CursorKind.CLASS_DECL or cursor.kind == CursorKind.STRUCT_DECL:
        process_class_or_struct(cursor, actual_file, actual_line)
    elif cursor.kind == CursorKind.FUNCTION_DECL:
        process_function(cursor, actual_file, actual_line)
    elif cursor.kind == CursorKind.ENUM_DECL:
        process_enum(cursor, actual_file, actual_line)

    for child in cursor.get_children():
        process_cursor(child) #TODO: optimize, file is already known

# Main script to parse .i file
parse_i_file_for_line_info(file_to_process)
index = clang.cindex.Index.create()
translation_unit = index.parse(file_to_process, args=['-x', 'c++', '-std=c++11',
                                                      '-fparse-all-comments',
                                                      '-fno-delayed-template-parsing',
                                                      '-ferror-limit=0'])
for child in translation_unit.cursor.get_children():
    process_cursor(child)
