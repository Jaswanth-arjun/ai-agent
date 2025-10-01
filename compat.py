# compat.py - Compatibility fixes for Werkzeug
import werkzeug.urls

# Add missing url_encode function if it doesn't exist
if not hasattr(werkzeug.urls, 'url_encode'):
    from urllib.parse import quote, urlencode
    
    def url_encode(query, encoding='utf-8'):
        if hasattr(query, 'items'):
            query = query.items()
        return urlencode([(k, v) for k, v in query], encoding=encoding)
    
    werkzeug.urls.url_encode = url_encode
