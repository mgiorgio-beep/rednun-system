s = open('marginedge_sync.py', 'r', encoding='utf-8').read()

old = '    # Store summary\n    for cat_type, data in category_totals.items():'
new = '    # Store summary - clear old data first\n    cursor.execute("DELETE FROM me_cogs_summary WHERE location = ?", (location,))\n    for cat_type, data in category_totals.items():'

if old in s:
    s = s.replace(old, new)
    open('marginedge_sync.py', 'w', encoding='utf-8').write(s)
    print('FIXED - ME COGS summary now clears before inserting')
else:
    print('ERROR - pattern not found')
