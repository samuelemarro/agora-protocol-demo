from flask import Flask, request
import requests as request_manager

app = Flask(__name__)

NO_HASH = 'no_hash'

DEFAULT_HANDLER = 'http://localhost:5001'

SUPPORTED_HANDLERS = {}

def call_handler(protocol_hash, data, disable_default_handler=False):
    # Call the handler for the protocol hash
    print(data)
    response = request_manager.post(SUPPORTED_HANDLERS.get(protocol_hash, DEFAULT_HANDLER), json=data)

    # Check if the query was successful
    if response.status_code != 200:
        if disable_default_handler:
            return {
                'status': response.status_code,
                'body': response.text
            }
        return call_handler(NO_HASH, data, disable_default_handler=True)

    return {
        'status': response.status_code,
        'body': response.text
    }

@app.route("/", methods=['POST'])
def main():
    data = request.get_json(force=True, silent=True)

    if data is None:
        # Parsing failed, treat as raw text
        data = request.data.decode('utf-8')
        protocol_hash = NO_HASH
    else:
        protocol_hash = data.get('protocolHash', NO_HASH)
        data = data.get('body', None)

    result = call_handler(protocol_hash, data)

    return result