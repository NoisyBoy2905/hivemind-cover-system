import sqlite3

connection = sqlite3.connect("hivemind.db")

with open("schema.sql") as f:
    schema_text = f.read()

connection.executescript(schema_text)

connection.close()

print("Database Created")