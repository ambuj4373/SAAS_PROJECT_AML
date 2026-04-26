import re

with open('app.py', 'r') as f:
    content = f.read()

# Fix the website URL markdown link
old_pattern = r'st\.markdown\(f"_Analyzed website: \[`\{_website_url\}`\]\(\{_website_url\}\)_"\)'
new_code = '''# Ensure proper URL format for links
                _proper_url = _website_url if _website_url.startswith(('http://', 'https://')) else f'https://{_website_url}'
                st.markdown(f"_Analyzed website: [`{_website_url}`]({_proper_url})_")'''

content = re.sub(old_pattern, new_code, content)

with open('app.py', 'w') as f:
    f.write(content)

print("✅ Fixed website URL links")
