import openai
from gremlin_python.driver import client, serializer
from dotenv import load_dotenv
import os

load_dotenv()

openai.api_type = "azure"
openai.api_key = os.getenv("OPENAI_API_KEY")
openai.base_url = "https://cpp-codebase-rag.openai.azure.com/openai/"
openai.api_version = "2024-08-01-preview"

# Gremlin DB connection setup
gremlin_client = client.Client('wss://cpp-codebase-playground.gremlin.cosmos.azure.com:443/', 'g',
                               username="/dbs/codebase/colls/codebase-graph",
                               password=os.getenv("COSMOS_DB_PRIMARY_KEY"),
                               message_serializer=serializer.GraphSONSerializersV2d0())

def get_vertex_labels():
    query = "g.V().label().dedup()"
    return gremlin_client.submit(query).all().result()

def get_edge_labels_for_vertex(label):
    query = f"g.V().hasLabel('{label}').outE().label().dedup()"
    return gremlin_client.submit(query).all().result()

def get_properties_for_vertex(label):
    query = f"g.V().hasLabel('{label}').valueMap(true).limit(1)"
    result = gremlin_client.submit(query).all().result()
    
    if result:
        # Extract property keys from the value map
        return list(result[0].keys())
    return []

def build_relationship_map():
    vertex_labels = get_vertex_labels()
    relationship_map = {}
    
    for label in vertex_labels:
        edges = get_edge_labels_for_vertex(label)
        relationship_map[label] = edges
    
    return relationship_map

def build_property_map():
    vertex_labels = get_vertex_labels()
    property_map = {}
    
    for label in vertex_labels:
        properties = get_properties_for_vertex(label)
        property_map[label] = properties
    
    return property_map

def build_gremlin_query_system_message():
    relationship_map = build_relationship_map()
    property_map = build_property_map()
    
    # Base system message
    system_message = """
    You are an expert in Azure Cosmos DB Gremlin query syntax. 
    Your task is to strictly generate an efficient Gremlin query to help with the user prompt.
    You are not to generate explanations or any other responses.
    Your response will be used by another LLM to answer the user prompt, you only generate query to extract additional info for the main LLM. 
    You have access to a graph database that represents a C++ codebase. 
    The database consists of the following vertex types and their associated edge types:
    """

    # Add the relationship map and property map to the system message
    for label in relationship_map.keys():
        system_message += f"\n- `{label}` vertices can have the following edges: {', '.join(relationship_map[label])}"
        system_message += f"\n  `{label}` vertices have the following properties: {', '.join(property_map[label])}"
    
    # Specify that the response should be an exact Gremlin query
    system_message += """    
        Important Notes:
        - Your response should be an exact Gremlin query and nothing else.
        - The query should extract as little data as possible, do not extract all vertices or edges properties but only relevant ones, group them with their unique identifiers (id)
        - Allowed Gremlin Steps:
    and, as, by, coalesce, constant
    count, dedup, drop, executionProfile, fold
    group, has, inject, is
    limit, local, not, optional, or, order
    path, project, properties, range
    repeat, sample, select, store
    TextP.startingWith(string), TextP.endingWith(string), TextP.containing(string), TextP.notStartingWith(string), TextP.notEndingWith(string), TextP.notContaining(string)
    tree, unfold, union
    V, E, out, in, both, outE, inE, bothE, outV, inV, bothV, otherV
    where  
        """
    return system_message

def generate_gremlin_query(user_request):
    # Get the enriched system message with metadata from the database
    system_message = build_gremlin_query_system_message()
    
    # Prepare the user message (the specific query request)
    user_message = f"Generate a Gremlin query to help with the {user_request}"

    # Interact with the LLM to generate the query
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        max_tokens=1000,
        temperature=0.2
    )
    
    # Extract the generated Gremlin query from the response
    query = response.choices[0].message.content
    cleaned_query = query.replace("```gremlin", "").replace("```", "").strip()
    return cleaned_query

def execute_gremlin_query(query):
    try:
        result = gremlin_client.submit(query).all().result()
        return result
    except Exception as e:
        print(f"Error executing query: {e}")
        return None

def generate_code_advisor_response(user_question, query_result):
    system_message = """
    You are an expert codebase advisor. Your task is to help answer technical questions about the codebase.
    The information you provide should be based on the data retrieved from the database in response to the user's query.
    
    Always tailor your response to the specific user question and the data provided.
    
    Guidelines for your response:
    - Make sure your answer directly addresses the user's question.
    - Use the database data to provide a precise and concise answer.
    - If appropriate, list relevant information (such as classes, methods, or properties) in a simple and easy-parseable format.
    - Do not add unnecessary details. Focus on answering the question clearly and succinctly.
    - Your answer should be structured in a way that the user can easily understand and use.
    """
    
    # Combine the user question and query result for the context
    query_summary = f"User's question: {user_question}\nData retrieved from the database: {query_result}"
    
    # Send the message to the OpenAI model to generate the answer
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": query_summary},
        ],
        max_tokens=2000,
        temperature=0.2
    )
    
    return response.choices[0].message.content

def main():
    user_request = "what namespaces exist in this package?"
    user_request = "what classes have 'exception' substring in their identifiers ?"
    user_request = "what classes inherit exception?"
    user_request = 'list all functions (including template) in namespace detail'
    user_request = "list me all functions (including template) to_json overloads and list of their parameters"

    gremlin_query = generate_gremlin_query(user_request)
    print(f"\nGenerated Gremlin Query:\n {gremlin_query}")

    query_result = execute_gremlin_query(gremlin_query)
    if query_result is None:
        print("Query execution failed.")
        return
    
    print(f"\nQuery result is:\n{query_result}")

    final_answer = generate_code_advisor_response(user_request, query_result)
    print(f"\nCode Advisor Answer:\n {final_answer}")

if __name__ == "__main__":
    main()
    gremlin_client.close()