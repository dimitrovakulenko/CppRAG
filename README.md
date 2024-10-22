## C++ Codebase AI Advisor with AST-based RAG

This repository contains a prototype for an **AI-powered chatbot agent** that answers questions about a C++ codebase. 

**Microsoft Azure OpenAI**-based Chatbot generates answers based on Abstract Syntax Tree (AST) of a C++ compilation unit as the primary source of information. 

Using AST for **retrieval-augmented generation (RAG)** enables precise complete answers about the codebase.

Code AST is a graph structure, retrieved using **clang** libraries, and partially stored in **Microsoft Azure Graph Cosmos DB**.

## Table of Contents
- [Overview](#overview)
- [Setup](#setup)
- [How It Works](#how-it-works)
- [Usage](#usage)
- [License](#license)

## Overview

This project consists of two main components:

1. **AST Processing & Storage**: A Python script that processes C++ source files using `clang`, extracts the AST (Abstract Syntax Tree), and stores a partial representation of the AST in **Azure Cosmos DB (Gremlin API)**.

2. **AI Chatbot Agent**: A Python-based chatbot agent that answers user questions about the C++ codebase by querying the AST stored in **Azure Cosmos DB** and using **Azure OpenAI** models to generate accurate answers.

## Setup

### Prerequisites
1. **Azure Cosmos DB (Gremlin API)**: You will need an Azure Cosmos DB instance with Gremlin API enabled for storing and querying the C++ AST.
2. **Azure OpenAI Service**: You'll need access to Azure's OpenAI services to run the chatbot.
3. **Clang**: Make sure `libclang` is available for parsing the C++ files.

### Steps to Set Up the Project:
1. **Clone the Repository**:
    ```bash
    git clone https://github.com/yourusername/yourrepository.git
    cd yourrepository
    ```

2. **Install Dependencies**:
    You can install the required dependencies using `pip`:
    ```bash
    pip install -r requirements.txt
    ```

3. **Set Up Azure Services**:
   - Set up **Azure Cosmos DB** and **Azure OpenAI** following their respective documentation.
   - Populate your `.env` file with the required connection information for **Azure Cosmos DB** and **Azure OpenAI API keys**.

4. **Run the C++ AST Processor**:
    - Ensure that you have a C++ source file ready for processing.
    - Run `process_cl_file_to_db.py` to fill the Cosmos DB with the AST:
    ```bash
    python process_cl_file_to_db.py
    ```

5. **Run the AI Chatbot**:
    - You can test the AI chatbot agent by running the `test_the_idea.py` script:
    ```bash
    python test_the_idea.py
    ```

## How It Works

### 1. **AST Processor (process_cl_file_to_db.py)**:
   - This script uses `clang` to parse the C++ source file and extract its AST.
   - The AST is then processed and only relevant parts of it (e.g., function declarations, classes, etc.) are stored in **Azure Cosmos DB (Gremlin API)**.
   - **Partial AST Representation**: The script only stores necessary AST elements, including classes, methods, fields, and relationships between them, such as inheritance.

### 2. **AI Chatbot Agent (test_the_idea.py)**:
   - The chatbot is designed to answer questions about the C++ codebase by querying the AST stored in Cosmos DB.
   - **Azure OpenAI** is used to:
     - **Generate Gremlin Queries**: The chatbot uses a GPT model to translate natural language questions into Gremlin queries.
     - **Provide Answers**: After retrieving the results from Cosmos DB, the model composes a coherent and informative answer to the user’s question.
   - **RAG**: The chatbot uses the retrieved graph data to augment the generated answers, ensuring accurate and relevant responses.

### Example Query:
- **User Request**: "List all classes in the namespace 'testing'."
- **Response**: The chatbot generates a Gremlin query, retrieves the list of classes from the database, and formats the response for the user.

## Usage

### Common Use Cases:
- Query the C++ codebase for classes, methods, or namespaces.
- Retrieve details about specific code elements or patterns within the codebase (e.g., functions containing a specific substring).
- Navigate relationships in the codebase, such as class inheritance or method definitions.

### Example Usage:
```bash
python test_the_idea.py
```

You can modify the `user_request` in the `test_the_idea.py` to explore different questions about the codebase.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
