import duckdb

con = duckdb.connect("data/warehouse/transit.duckdb")

print(con.execute("""
DESCRIBE shape_distance_summary
""").fetchdf())

con.close()