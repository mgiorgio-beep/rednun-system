s = open('analytics.py', 'r', encoding='utf-8').read()

# Fix the broken filter
old = "server_guid IS NOT NULL AND o.server_guid !=  AND (e.wage IS NULL OR e.wage = 0) AND e.first_name NOT LIKE ''Host%%''"

new = "server_guid IS NOT NULL AND o.server_guid != '' AND (e.wage IS NULL OR e.wage = 0) AND e.first_name NOT LIKE 'Host%'"

if old in s:
    s = s.replace(old, new)
    open('analytics.py', 'w', encoding='utf-8').write(s)
    print('FIXED server filter')
else:
    print('Pattern not found - checking...')
    idx = s.find('server_guid IS NOT NULL')
    if idx >= 0:
        print(repr(s[idx:idx+200]))
    else:
        print('server_guid filter not found at all')
