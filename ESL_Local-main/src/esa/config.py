import os
from dotenv import load_dotenv
load_dotenv()
def setting(name, default=None):
    return os.getenv(name, default)
