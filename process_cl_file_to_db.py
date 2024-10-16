import clang.cindex
from clang.cindex import CursorKind
from gremlin_python.driver import client, serializer
from dotenv import load_dotenv
import os
import re

#==================================================================================
# 0. Input
#==================================================================================

repo_path = "C:/Users/vakul/source/repos/googletest/"
file_to_process = repo_path + "googletest/src/gtest-all.cc"

include_folders = f"{repo_path}googletest/include; {repo_path}/googletest"
defines = "_UNICODE;UNICODE;WIN32;_WINDOWS;_WIN32;STRICT;WIN32_LEAN_AND_MEAN;GTEST_HAS_PTHREAD=0;_HAS_EXCEPTIONS=1"

def parse_input(include_folders, defines):
    args = []

    # Process include paths
    include_list = [inc.strip() for inc in include_folders.split(';') if inc.strip()]
    for include in include_list:
        args.append('-I')
        args.append(include)

    # Process defines
    define_list = [define.strip() for define in defines.split(';') if define.strip()]
    for define in define_list:
        args.append('-D')
        args.append(define)

    return args

#==================================================================================
# 1. Initial setup
#==================================================================================

# Load environment variables
load_dotenv()

# Set up clang index library
clang.cindex.Config.set_library_file('C:\\Program Files\\LLVM\\bin\\libclang.dll')

# Set up Gremlin client
gremlin_client = client.Client('wss://example-opencv.gremlin.cosmos.azure.com:443/', 'g',
                               username="/dbs/codebase/colls/codebase-graph",
                               password=os.getenv("COSMOS_DB_PRIMARY_KEY"),
                               message_serializer=serializer.GraphSONSerializersV2d0())

#==================================================================================
# 2. Gremlin-related methods
#==================================================================================

# Will make relative file path and will remove unsupported symbols
def get_file_id(file_path, replacement_symbol = '_'):
    if file_path.startswith(repo_path):
        relative_path = os.path.relpath(file_path, repo_path)
    else:
        relative_path = file_path.replace("C:/Program Files/", "system/")
    
    adjusted_path = relative_path.replace('\\', replacement_symbol).replace('/', replacement_symbol)
    return adjusted_path

# Get id of a cursor for the database 
def get_id(cursor, replacement_symbol = '_'):
    id = cursor.get_usr()
    if id:
        id = id.replace('#', replacement_symbol)
        if not cursor.is_definition():
            id = id + cursor.location.file.name + cursor.location.line
    if not id:
        raise ValueError("ID cannot be empty!")
    return id

def vertex_exists(cursor):
    query = "g.V().has('id', id).count()"
    bindings = {"id": get_id(cursor)}
    
    # Execute the query and get the result
    result = gremlin_client.submit(query, bindings).all().result()
    
    # Return True if the count is greater than 0, meaning the vertex exists
    return result[0] > 0

tu_cached = None

# Add vertex to Gremlin database
def add_vertex_to_gremlin(label, cursor, properties = {}):
    properties["id"] = get_id(cursor)
    if tu_cached is None:
        tu_cached = get_file_id(cursor.translation_unit.spelling)
    properties["tu"] = tu_cached

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

#==================================================================================
# 3. Adding properties for specific types
#==================================================================================

#==================================================================================
# 4. Processing
#==================================================================================

# Function to process cursor based on its kind
def process_cursor(cursor):
    try:
        if cursor.is_definition():
            if cursor.kind == clang.cindex.CursorKind.FUNCTION_DECL:
                print(f"Found a function declaration: {cursor.spelling}")
            elif cursor.kind == clang.cindex.CursorKind.CLASS_DECL:
                print(f"Found a class declaration: {cursor.spelling}")
            elif cursor.kind == clang.cindex.CursorKind.VAR_DECL:
                print(f"Found a variable declaration: {cursor.spelling}")
            # Add more cases as needed for other cursor types
        # Example: Process cursor references
        if cursor.kind == clang.cindex.CursorKind.TYPE_REF:
            referenced_cursor = cursor.referenced
            print(f"Type reference: {referenced_cursor.spelling}")
    except Exception as e:
        print(f"Error processing cursor at {cursor.location.line}: {e}")

# Create translation unit
index = clang.cindex.Index.create()
clang_args = parse_input(include_folders, defines)
translation_unit = index.parse(file_to_process, args=[
    '-x', 'c++',            # Specify C++ language
    '-std=c++11',           # Set the C++ standard (adjust if needed)
    '-fparse-all-comments', # Optional flags
    '-fno-delayed-template-parsing',
    '-ferror-limit=0'
] + clang_args)

# Walk the entire translation unit in preorder
for cursor in translation_unit.cursor.walk_preorder():
    process_cursor(cursor)