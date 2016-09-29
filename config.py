import json

defaults = {
    "database": "./db",
    "image_dir": "."
}

def get(key):
    try:
        cfg = json.load(open("config.json"))
    except FileNotFoundError:
        cfg = {}
    if key in cfg:
        return cfg[key]
    return defaults[key]
