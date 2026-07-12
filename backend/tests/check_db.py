import pymysql

conn = pymysql.connect(
    host='127.0.0.1', port=3306, user='root',
    password='123456', database='xunfei07_rag_db', charset='utf8mb4'
)
cur = conn.cursor()

# 1. 各域各字段的分布
print("=== 各域各字段分布 ===")
cur.execute("""
    SELECT domain, source_field, COUNT(*) as cnt,
           MIN(LENGTH(content)) as min_len,
           MAX(LENGTH(content)) as max_len,
           ROUND(AVG(LENGTH(content))) as avg_len
    FROM text_chunks
    GROUP BY domain, source_field
    ORDER BY domain, source_field
""")
for row in cur.fetchall():
    print(f"  {str(row[0]):15s} {str(row[1]):20s} count={row[2]:6d}  min={row[3]:4d}  max={row[4]:4d}  avg={row[5]}")

# 2. 各条件的实际数量
print("\n=== 各查询条件实际数量 ===")
checks = [
    ("tender + content + len>100", "SELECT COUNT(*) FROM text_chunks WHERE domain='tender' AND source_field='content' AND LENGTH(content)>100"),
    ("enterprise + content + len>100", "SELECT COUNT(*) FROM text_chunks WHERE domain='enterprise' AND source_field='content' AND LENGTH(content)>100"),
    ("policy + content + len>100", "SELECT COUNT(*) FROM text_chunks WHERE domain='policy' AND source_field='content' AND LENGTH(content)>100"),
    ("enterprise + business_scope + len>100", "SELECT COUNT(*) FROM text_chunks WHERE domain='enterprise' AND source_field='business_scope' AND LENGTH(content)>100"),
    ("title/project_name + len>20", "SELECT COUNT(*) FROM text_chunks WHERE domain IN ('tender','enterprise','policy') AND source_field IN ('title','project_name') AND LENGTH(content)>20"),
    ("ALL content + len>50", "SELECT COUNT(*) FROM text_chunks WHERE source_field='content' AND LENGTH(content)>50"),
    ("ALL content + len>20", "SELECT COUNT(*) FROM text_chunks WHERE source_field='content' AND LENGTH(content)>20"),
]
for label, sql in checks:
    cur.execute(sql)
    print(f"  {label:40s} => {cur.fetchone()[0]} 条")

conn.close()
