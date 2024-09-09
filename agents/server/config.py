import json
import os
import random

import requests as request_manager

import databases.mongo as mongo
from toolformers.base import Tool, StringParameter

TOOLS = []
ADDITIONAL_INFO = ''
NODE_URLS = {}

def get_additional_info():
    return ADDITIONAL_INFO

def add_mongo_database(internal_name, external_name, schema):
    global ADDITIONAL_INFO
    # TODO: Support hot-loading?
    mongo.create_database_from_schema(external_name, schema)

    ADDITIONAL_INFO += f'You have access to a database named {internal_name}.\n'
    ADDITIONAL_INFO += 'The database has the following collections (with schemas):\n\n'

    for collection_name, collection_schema in schema['collections'].items():
        ADDITIONAL_INFO += f'=={collection_name}==\n'

        if 'startingValues' in collection_schema:
            del collection_schema['startingValues']

        ADDITIONAL_INFO += json.dumps(collection_schema, indent=2) + '\n\n'

def add_mongo_tools(name_mappings):
    def insert_element(database, collection, doc):
        doc = json.loads(doc)
        external_name = name_mappings[database]
        mongo.insert_one(external_name, collection, doc)
        return 'Done'

    insert_element_tool = Tool('insert_into_database', 'Insert into a database (MongoDB).', [
        StringParameter('database', 'The database to insert into', True),
        StringParameter('collection', 'The collection to insert into', True),
        StringParameter('doc', 'The document to insert, as a formatted JSON string for MongoDB', True)
    ], insert_element)

    def query_database(database, collection, query):
        query = json.loads(query)
        external_name = name_mappings[database]
        output = mongo.query_database(external_name, collection, query)
        print(output)
        return json.dumps(output)

    find_in_database_tool = Tool('find_in_database', 'Find in a database (MongoDB). Returns a JSON formatted string with the result.', [
        StringParameter('database', 'The database to query', True),
        StringParameter('collection', 'The collection to query', True),
        StringParameter('query', 'The query to run, as a formatted JSON string for MongoDB', True)
    ], query_database)

    def update_element(database, collection, query, update):
        query = json.loads(query)
        update = json.loads(update)
        external_name = name_mappings[database]
        mongo.update_one(external_name, collection, query, update)
        return 'Done'

    update_element_tool = Tool('update_one_in_database', 'Update an element in a database (MongoDB).', [
        StringParameter('database', 'The database to query', True),
        StringParameter('collection', 'The collection to query', True),
        StringParameter('query', 'The query to run, as a formatted JSON string for MongoDB', True),
        StringParameter('update', 'The update to run, as a formatted JSON string for MongoDB. Remember the $ operator (e.g. {$set : {...}})', True)
    ], update_element)

    def delete_element(database, collection, query):
        query = json.loads(query)
        external_name = name_mappings[database]
        mongo.delete_one(external_name, collection, query)
        return 'Done'

    delete_element_tool = Tool('delete_element_in_database', 'Delete an element in a database (MongoDB).', [
        StringParameter('database', 'The database to query', True),
        StringParameter('collection', 'The collection to query', True),
        StringParameter('query', 'The query to run, as a formatted JSON string for MongoDB', True)
    ], delete_element)

    TOOLS.append(insert_element_tool)
    TOOLS.append(find_in_database_tool)
    TOOLS.append(update_element_tool)
    TOOLS.append(delete_element_tool)

    # TODO: Test insert and delete tools

def prepare_mock_tool(tool_schema, internal_name):
    input_schema = tool_schema['input']

    required_parameter_names = input_schema['required']
    parameters = []

    for parameter_name, parameter_data in input_schema['properties'].items():
        if parameter_data['type'] == 'string':
            parameters.append(StringParameter(parameter_name, parameter_data['description'], parameter_name in required_parameter_names))
        else:
            raise ValueError('Unknown parameter type:', parameter_data['type'])

    def run_mock_tool(*args, **kwargs):
        return json.dumps(random.choice(tool_schema['mockValues']))

    mock_tool = Tool(internal_name, tool_schema['description'], [], run_mock_tool, output_schema=tool_schema['output'])

    return mock_tool

def prepare_external_tool(tool_schema, internal_name, external_server_name):
    input_schema = tool_schema['input']

    required_parameter_names = input_schema['required']
    parameters = []

    for parameter_name, parameter_data in input_schema['properties'].items():
        if parameter_data['type'] == 'string':
            parameters.append(StringParameter(parameter_name, parameter_data['description'], parameter_name in required_parameter_names))
        else:
            raise ValueError('Unknown parameter type:', parameter_data['type'])
        
    helper_id = os.environ.get('AGENT_ID') + '_helper'
    helper_url = NODE_URLS[helper_id]

    def run_external_tool(*args, **kwargs):
        for i, arg in enumerate(args):
            argument_name = parameters[i].name
            kwargs[argument_name] = arg

        # TODO: Check that the parameter names are correct?

        print('Running external tool:', internal_name, kwargs)
        query_parameters = {
            'type' : internal_name, # TODO: Use the schema name instead? Must be matched on the other side
            'data' : kwargs,
            'targetServer' : external_server_name
        }
        response = request_manager.post(helper_url + '/customRun', json=query_parameters)

        print('Response from external tool:', response.text)

        if response.status_code == 200:
            parsed_response = json.loads(response.text)

            return json.dumps(parsed_response)
            #if parsed_response['status'] == 'success':
            #    return parsed_response['body']
        return 'Failed to call the tool: ' + response.text

    external_tool = Tool(internal_name, tool_schema['description'], parameters, run_external_tool, output_schema=tool_schema['output'])

    return external_tool

def load_config(server_name):
    global ADDITIONAL_INFO
    with open('config.json') as f:
        config = json.load(f)

    with open('node_urls.json') as f:
        node_urls = json.load(f)
    for node_id, node_url in node_urls.items():
        NODE_URLS[node_id] = node_url
    
    server_config = config['servers'][server_name]

    ADDITIONAL_INFO += 'This is the description of the service provided by the server:\n====\n'
    ADDITIONAL_INFO += server_config['description']

    for action_description in server_config['actionDescriptions']:
        ADDITIONAL_INFO += f'\n- {action_description}'
    
    ADDITIONAL_INFO += '\n====\n\n'

    databases = []

    if server_config['internalDbSchema'] is not None:
        databases.append(('internalDb', server_name, server_config['internalDbSchema']))
    
    databases += [(x, x, x) for x in server_config.get('externalDbs', [])]
    
    has_mongo_db = False
    for internal_name, external_name, schema_name in databases:
        schema = config['dbSchemas'][schema_name]
        if schema['dbType'] == 'mongo':
            add_mongo_database(internal_name, external_name, schema)
            has_mongo_db = True
    
    if has_mongo_db:
        add_mongo_tools({x[0]: x[1] for x in databases})

    for internal_name, schema_name in server_config.get('mockTools', {}).items():
        tool_schema = config['toolSchemas'][schema_name]
        tool = prepare_mock_tool(tool_schema, internal_name)
        TOOLS.append(tool)

    for internal_name, external_tool_config in server_config.get('externalTools', {}).items():
        schema_name = external_tool_config['schema']
        tool_schema = config['toolSchemas'][schema_name]
        tool_server = external_tool_config['server']
        tool = prepare_external_tool(tool_schema, internal_name, tool_server)
        TOOLS.append(tool)

    print('Final additional info:', ADDITIONAL_INFO)