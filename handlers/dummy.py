from flask import Flask, request
import requests as request_manager

app = Flask(__name__)

@app.route("/", methods=['POST'])
def main():
    return 'Hello, World!'