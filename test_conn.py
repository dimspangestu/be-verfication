from dotenv import load_dotenv
load_dotenv()

import os
import pymssql

server = os.getenv("MSSQL_SERVER")
port = int(os.getenv("MSSQL_PORT", "1433"))
db = os.getenv("MSSQL_DATABASE")
user = os.getenv("MSSQL_USER")
pw = os.getenv("MSSQL_PASSWORD")

print("TRY:", server, port, db, user, "pw_len=", len(pw or ""))

conn = pymssql.connect(server=server, user=user, password=pw, database=db, port=port)
print("OK CONNECT")
conn.close()