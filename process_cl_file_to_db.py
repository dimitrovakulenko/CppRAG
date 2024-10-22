#############################################
# This script allows processing of a single
# C++ translation unit AST (abstract syntax tree) 
# to an Azure Cosmos Graph DB.
#############################################

import clang.cindex
from clang.cindex import CursorKind
from gremlin_python.driver import client, serializer
from dotenv import load_dotenv
import os

#############################################
### 0. Input
#############################################
# Specify C++ compilation file to process, additional include folders it might require and defines.
# Don't forget to specify COSMOS_DB_PRIMARY_KEY as an environment variable (you can use .env file)
#############################################

repo_path = "C:/Users/vakul/source/repos/json/"
file_to_process = repo_path + "include/nlohmann/json.hpp"

include_folders = f"{repo_path}include"
defines = "_UNICODE;UNICODE;WIN32;_WINDOWS;_WIN32;STRICT;_MBCS;JSON_TEST_KEEP_MACROS;"

cosmos_db_url = 'wss://cpp-codebase-playground.gremlin.cosmos.azure.com:443/'
cosmos_db_username = "/dbs/codebase/colls/codebase-graph"

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

#############################################
### 1. Initial setup
#############################################

# Load environment variables
load_dotenv()

# Set up clang index library
clang.cindex.Config.set_library_file('C:\\Program Files\\LLVM\\bin\\libclang.dll')

# Set up Gremlin client
gremlin_client = client.Client(cosmos_db_url, 'g',
                               username=cosmos_db_username,
                               password=os.getenv("COSMOS_DB_PRIMARY_KEY"),
                               message_serializer=serializer.GraphSONSerializersV2d0())

#############################################
### 2. Gremlin-related methods
#############################################

# Will make relative file path and will remove unsupported symbols
def get_file_id(file_path, replacement_symbol = '_'):
    if file_path.startswith(repo_path):
        relative_path = os.path.relpath(file_path, repo_path)
    else:
        relative_path = os.path.basename(file_path)
    
    adjusted_path = relative_path.replace('\\', replacement_symbol).replace('/', replacement_symbol)
    return adjusted_path

# Get id of a cursor for the database 
def get_id(cursor, replacement_symbol = '_'):
    id = cursor.get_usr()
    if id:
        id = id.replace('#', replacement_symbol + replacement_symbol)
        id = id.replace('/', replacement_symbol + replacement_symbol + replacement_symbol)
        if not cursor.is_definition():
            if cursor == cursor.canonical:
                id = f"{id}"
            else:
                id = f"{id}@{get_file_id(cursor.location.file.name)}@{cursor.location.line}"
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
def add_vertex_to_gremlin(cursor, id, properties = {}):
    global tu_cached

    properties["id"] = id
    properties["label"] = cursor.kind.name
    properties["usr"] = cursor.get_usr()
    properties["spelling"] = cursor.spelling
    if tu_cached is None:
        tu_cached = get_file_id(cursor.translation_unit.spelling)
    properties["tu"] = tu_cached
    properties["file"] = get_file_id(cursor.location.file.name) #TODO: add file as a vertex?
    properties["line"] = cursor.location.line

    properties_pairs = [(key, value) for key, value in properties.items()]
    query = "g.addV(label)"
    bindings = {}

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

#############################################
### 3. Adding properties for specific types
#############################################

def add_is_static_storage_property(cursor, properties):
    properties['is_static'] = cursor.storage_class == clang.cindex.StorageClass.STATIC

def add_is_exported_property(cursor, properties):
    properties['is_exported'] = cursor.linkage == clang.cindex.LinkageKind.EXTERNAL

def add_access_specifier(cursor, properties):
    access_specifier_map = {
        clang.cindex.AccessSpecifier.PUBLIC: "public",
        clang.cindex.AccessSpecifier.PRIVATE: "private",
        clang.cindex.AccessSpecifier.PROTECTED: "protected",
        clang.cindex.AccessSpecifier.INVALID: "none"
    }
    properties['access_specifier'] = access_specifier_map.get(cursor.access_specifier, "none")

def get_field_properties(cursor, properties):
    properties = {}
    add_is_exported_property(cursor, properties)
    add_is_static_storage_property(cursor, properties)
    add_access_specifier(cursor, properties)
    return properties

def get_member_function_properties(cursor):
    properties = {
        'is_const': cursor.is_const_method(),
        'is_static': cursor.is_static_method(),
        'is_virtual': cursor.is_virtual_method(),
        'is_pure_virtual': cursor.is_pure_virtual_method(),
        'is_defaulted': cursor.is_default_method(),
        'is_deleted': cursor.is_deleted_method()
    }
    add_is_exported_property(cursor, properties)
    add_access_specifier(cursor, properties)
    return properties

def get_constructor_properties(cursor):
    properties = {
        'is_copy_constructor': cursor.is_copy_constructor(),
        'is_default_constructor': cursor.is_default_constructor(),
        'is_move_constructor': cursor.is_move_constructor(),
        'is_converting_constructor': cursor.is_converting_constructor()
    }
    add_access_specifier(cursor, properties)
    return properties

def get_destructor_properties(cursor):
    properties = {
        'is_virtual': cursor.is_virtual_method(),
        'is_pure_virtual': cursor.is_pure_virtual_method(),
        'is_deleted': cursor.is_deleted_method(),
        'is_defaulted': cursor.is_default_method(),
    }
    add_is_exported_property(cursor, properties)
    add_access_specifier(cursor, properties)
    return properties

def get_conversion_function_properties(cursor):
    properties = {
        'is_explicit': cursor.is_explicit_method(),
        'is_virtual': cursor.is_virtual_method(),
        'is_pure_virtual': cursor.is_pure_virtual_method(),
        'is_defaulted': cursor.is_default_method(),
        'is_deleted': cursor.is_deleted_method(),
    }
    add_is_exported_property(cursor, properties)
    add_access_specifier(cursor, properties)
    return properties

#############################################
### 4. Processing
#############################################

processed_cursors_ids = set()

# Add vertex for cursor
def process_cursor_as_vertex(cursor, processed_cursors, lexical_parent=None):
    try:
        if cursor.kind.is_declaration():
            id = get_id(cursor)
            if id and not id in processed_cursors_ids:
                processed_cursors_ids.add(id)
                processed_cursors.append(cursor)

                properties = {}

                if cursor.is_definition():
                    if (cursor.kind is CursorKind.STRUCT_DECL or 
                        cursor.kind is CursorKind.UNION_DECL or
                        cursor.kind is CursorKind.CLASS_DECL or
                        cursor.kind is CursorKind.ENUM_DECL):
                        add_is_exported_property(cursor, properties)
                    elif cursor.kind is CursorKind.FIELD_DECL:
                        properties = get_field_properties(cursor, properties)
                    elif cursor.kind is CursorKind.ENUM_CONSTANT_DECL:
                        pass
                    elif cursor.kind is CursorKind.FUNCTION_DECL:
                        add_is_exported_property(cursor, properties)
                    elif cursor.kind is CursorKind.VAR_DECL:
                        add_is_static_storage_property(cursor, properties)
                    elif cursor.kind is CursorKind.PARM_DECL:
                        pass
                    elif cursor.kind is CursorKind.TYPEDEF_DECL:
                        pass
                    elif cursor.kind is CursorKind.CXX_METHOD:
                        properties = get_member_function_properties(cursor)
                    elif cursor.kind is CursorKind.NAMESPACE:
                        pass
                    elif cursor.kind is CursorKind.LINKAGE_SPEC:
                        return
                    elif cursor.kind is CursorKind.CONSTRUCTOR:
                        properties = get_constructor_properties(cursor)
                    elif cursor.kind is CursorKind.DESTRUCTOR:
                        properties = get_destructor_properties(cursor)
                    elif cursor.kind is CursorKind.CONVERSION_FUNCTION:
                        properties = get_conversion_function_properties(cursor)
                    elif (cursor.kind is CursorKind.TEMPLATE_TYPE_PARAMETER or
                        cursor.kind is CursorKind.TEMPLATE_NON_TYPE_PARAMETER or
                        cursor.kind is CursorKind.TEMPLATE_TEMPLATE_PARAMETER or
                        cursor.kind is CursorKind.FUNCTION_TEMPLATE or
                        cursor.kind is CursorKind.CLASS_TEMPLATE or
                        cursor.kind is CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION):
                        pass
                    elif (cursor.kind is CursorKind.NAMESPACE_ALIAS or
                        cursor.kind is CursorKind.USING_DIRECTIVE or
                        cursor.kind is CursorKind.USING_DECLARATION or
                        cursor.kind is CursorKind.TYPE_ALIAS_DECL or
                        cursor.kind is CursorKind.TYPE_ALIAS_TEMPLATE_DECL):
                        pass
                    else:
                        return

                add_vertex_to_gremlin(cursor, id, properties)
        elif cursor.kind.is_reference():
            processed_cursors.append(cursor)

            id = get_id(cursor.referenced)
            if id:
                if not id in processed_cursors_ids:
                    processed_cursors_ids.add(id)
                    add_vertex_to_gremlin(cursor.referenced, id, {})
                if cursor.kind == CursorKind.CXX_BASE_SPECIFIER:
                    # Workaround, cursor.lexical_parent is sometimes None
                    add_edge_to_gremlin(lexical_parent, 'inherits', cursor.referenced)

        elif cursor.kind.is_expression():
            pass
        elif cursor.kind.is_statement():
            pass
    except Exception as e:
        print(f"ERROR processing cursor at {cursor.location.file.name}:{cursor.location.line}: {e}")

    for child in cursor.get_children():        
        process_cursor_as_vertex(child, processed_cursors, cursor)

# Add edges for cursor
def process_cursor_edges(cursor):
    try:
        if cursor.kind.is_declaration():
            if cursor.is_definition():
                if (cursor.kind is CursorKind.STRUCT_DECL or 
                    cursor.kind is CursorKind.UNION_DECL or
                    cursor.kind is CursorKind.CLASS_DECL or
                    cursor.kind is CursorKind.CLASS_TEMPLATE or
                    cursor.kind is CursorKind.ENUM_DECL):
                    if cursor.semantic_parent:
                        if cursor.semantic_parent.kind == CursorKind.NAMESPACE:
                            add_edge_to_gremlin(cursor.semantic_parent, 'contains', cursor)
                        elif cursor.semantic_parent.kind in (CursorKind.STRUCT_DECL, CursorKind.CLASS_DECL):
                            add_edge_to_gremlin(cursor.semantic_parent, 'contains_inner', cursor)
                elif (cursor.kind is CursorKind.NAMESPACE or
                      cursor.kind is CursorKind.FUNCTION_DECL or
                      cursor.kind is CursorKind.FUNCTION_TEMPLATE):
                    if cursor.semantic_parent:
                        if cursor.semantic_parent.kind == CursorKind.NAMESPACE:
                            add_edge_to_gremlin(cursor.semantic_parent, 'contains', cursor)
                elif (cursor.kind is CursorKind.CXX_METHOD or
                    cursor.kind is CursorKind.CONSTRUCTOR or
                    cursor.kind is CursorKind.DESTRUCTOR or
                    cursor.kind is CursorKind.CONVERSION_FUNCTION):
                    add_edge_to_gremlin(cursor.semantic_parent, 'contains_method', cursor.get_definition())
                elif cursor.kind is CursorKind.FIELD_DECL:
                    add_edge_to_gremlin(cursor.semantic_parent, 'contains_field', cursor)
                elif cursor.kind is CursorKind.PARM_DECL:
                    add_edge_to_gremlin(cursor.semantic_parent, 'contains_argument', cursor)
                elif cursor.kind is CursorKind.ENUM_CONSTANT_DECL:
                    add_edge_to_gremlin(cursor.semantic_parent, 'contains_value', cursor)
                #elif cursor.kind is CursorKind.TYPEDEF_DECL:
                    #tp = cursor.underlying_typedef_type.get_declaration()
            else:
                add_edge_to_gremlin(cursor, 'declares', cursor.canonical)
        elif cursor.kind.is_reference():
            pass
        elif cursor.kind.is_expression():
            pass
        elif cursor.kind.is_statement():
            pass
    except Exception as e:
        print(f"ERROR processing cursor edges at {cursor.location.file.name}:{cursor.location.line}: {e}")

# Process translation unit 
def process_translation_unit(tu):
    processed_cursors = []

    # create nodes
    for cursor in tu.cursor.get_children():
        try:
            if cursor.location.file is None or "Program Files" in cursor.location.file.name:
                pass
            elif cursor.kind.is_invalid():
                raise ValueError('Invalid kind of cursor')
            elif cursor.kind.is_unexposed():
                pass #raise ValueError('Unexposed kind of cursor') ?
            elif cursor.kind.is_translation_unit():
                pass
            else:
                process_cursor_as_vertex(cursor, processed_cursors)
        except Exception as e:
            print(f"ERROR processing cursor at {cursor.location.file.name}:{cursor.location.line}: {e}")
    
    # create edges
    for cursor in processed_cursors:
        try:
            if cursor.location.file is None or "Program Files" in cursor.location.file.name:
                pass
            elif cursor.kind.is_invalid():
                raise ValueError('Invalid kind of cursor')
            elif cursor.kind.is_unexposed():
                pass #raise ValueError('Unexposed kind of cursor') ?
            elif cursor.kind.is_translation_unit():
                pass
            else:
                process_cursor_edges(cursor)
        except Exception as e:
            print(f"ERROR processing cursor edges at {cursor.location.file.name}:{cursor.location.line}: {e}")

#############################################
### 5. Main
#############################################

# Create translation unit
index = clang.cindex.Index.create()
clang_args = parse_input(include_folders, defines)
translation_unit = index.parse(file_to_process, args=[
    '-x', 'c++',
    '-std=c++11', 
    '-fparse-all-comments',
    '-fno-delayed-template-parsing',
    '-ferror-limit=0'
] + clang_args)

# Walk the entire translation unit in preorder
process_translation_unit(translation_unit)

gremlin_client.close()