import json


def json_read(file):
    with open(file, 'r') as f:
        data = json.load(f)
        return data

def json_write(file, data):
    with open(file, 'w') as f:
        json.dump(data, f, indent=4)

def json_to_string(data):
    return json.dumps(data)