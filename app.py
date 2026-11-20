import os
import logging
import base64
import hashlib
import io
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from flask import Flask, render_template, request, jsonify, send_file, Response, abort, redirect, url_for, session, g, make_response
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from storage_providers import get_storage_provider
import mimetypes
import boto3
from botocore.exceptions import ClientError
from functools import wraps
import secrets
import json
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
import re

load_dotenv()

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = Flask(__name__)
is_production = os.environ.get('PRODUCTION', 'false').lower() == 'true'
if is_production and not os.environ.get('FLASK_SECRET_KEY'):
    raise RuntimeError('Set a persistent FLASK_SECRET_KEY before starting in production.')
if (is_production or os.environ.get('S3_BUCKET')) and not os.environ.get('APP_PASSWORD'):
    raise RuntimeError('Set APP_PASSWORD to protect the storage manager.')

app.secret_key = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=int(os.environ.get('MAX_UPLOAD_MB', '100')) * 1024 * 1024,
    FORCE_HTTPS=os.environ.get('FORCE_HTTPS', 'false').lower() == 'true',
    APP_USERNAME=os.environ.get('APP_USERNAME', 'admin'),
    APP_PASSWORD=os.environ.get('APP_PASSWORD', ''),
    S3_BUCKET=os.environ.get('S3_BUCKET', ''),
    AWS_REGION=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    WTF_CSRF_TIME_LIMIT=None,
)
app.config['SESSION_COOKIE_SECURE'] = app.config['FORCE_HTTPS']
if os.environ.get('TRUST_PROXY_HEADERS', 'false').lower() == 'true':
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Flask's signed session cookie is readable by its owner. Encrypt provider keys
# before putting them in that cookie, using the persistent application secret.
credential_cipher = Fernet(base64.urlsafe_b64encode(
    hashlib.sha256(('storage-provider:' + app.secret_key).encode()).digest()
))

# Initialize CSRF protection
csrf = CSRFProtect()
csrf.init_app(app)

@app.before_request
def before_request():
    # Load balancers must be able to check liveness without credentials or TLS.
    if request.path == '/health':
        return None
    if app.config['FORCE_HTTPS'] and not request.is_secure:
        return redirect(request.url.replace('http://', 'https://', 1), code=308)
    password = app.config['APP_PASSWORD']
    if password:
        auth = request.authorization
        if not (auth and auth.type == 'basic'
                and secrets.compare_digest((auth.username or '').encode(), app.config['APP_USERNAME'].encode())
                and secrets.compare_digest((auth.password or '').encode(), password.encode())):
            return Response('Authentication required', status=401,
                            headers={'WWW-Authenticate': 'Basic realm="Storage Manager"'})

@app.after_request
def after_request(response):
    # Ensure CSRF token is set in cookie and header
    if request.path != '/health' and response.status_code != 401 and not request.path.startswith('/static/'):
        csrf_token = generate_csrf()
        response.set_cookie('csrf_token', csrf_token, samesite='Strict', secure=request.is_secure)
        response.headers['X-CSRF-Token'] = csrf_token
    return response

@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    return jsonify({'error': error.description}), 400

@app.errorhandler(413)
def handle_large_upload(error):
    return jsonify({'error': 'File exceeds the configured upload limit.'}), 413

@app.route('/health')
def health():
    return jsonify({'status': 'ok'}), 200

@app.route('/health/storage')
def storage_health():
    provider = get_current_provider()
    if not provider:
        return jsonify({'status': 'unconfigured'}), 503
    try:
        if hasattr(provider, 'check_connection'):
            provider.check_connection()
        else:
            provider.list_files()
        return jsonify({'status': 'ok'}), 200
    except Exception:
        logger.exception('Storage health check failed')
        return jsonify({'status': 'unavailable'}), 503

@app.route('/get-csrf-token')
def get_csrf_token():
    token = generate_csrf()
    response = make_response(jsonify({'csrf_token': token}))
    response.set_cookie('csrf_token', token, samesite='Strict', secure=request.is_secure)
    return response

# Set up logging
logger = logging.getLogger(__name__)

CHUNK_SIZE = 100 * 1024 * 1024  # 100 MB chunks

# Update MIME type detection
mimetypes.init()
mimetypes.add_type('image/webp', '.webp')
mimetypes.add_type('image/heic', '.heic')
mimetypes.add_type('image/heif', '.heif')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated') and not app.config['S3_BUCKET']:
            return redirect(url_for('configure_storage'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_provider():
    """Get the current storage provider based on session configuration"""
    try:
        if app.config['S3_BUCKET']:
            session['bucket'] = app.config['S3_BUCKET']
            return get_storage_provider('aws', bucket=app.config['S3_BUCKET'],
                                        region=app.config['AWS_REGION'])
        if 'provider_type' not in session:
            return None
        credentials = json.loads(credential_cipher.decrypt(session['provider_config'].encode()))
        return get_storage_provider(
            session['provider_type'],
            **credentials
        )
    except Exception as e:
        logger.error(f"Error creating storage provider: {str(e)}")
        return None

@app.route('/')
@login_required
def index():
    provider = get_current_provider()
    if not provider:
        if app.config['S3_BUCKET']:
            return jsonify({'error': 'Unable to initialize the configured S3 provider'}), 503
        return redirect(url_for('configure_storage'))
    return render_template('index.html')

@app.route('/configure', methods=['GET', 'POST'])
def configure_storage():
    if app.config['S3_BUCKET']:
        return redirect(url_for('index'))
    if request.method == 'POST':
        provider_type = request.form.get('provider_type')
        logger.debug(f"Received configuration request for provider: {provider_type}")
        
        try:
            if provider_type == 'cloudflare':
                account_id = request.form.get('account_id', '').strip()
                access_key = request.form.get('access_key', '').strip()
                secret_key = request.form.get('secret_key', '').strip()
                bucket = request.form.get('bucket', '').strip()

                logger.debug(f"Cloudflare configuration - Account ID: {account_id}, Bucket: {bucket}")

                if not account_id:
                    return jsonify({'error': 'Account ID is required'}), 400
                if not access_key:
                    return jsonify({'error': 'Access Key ID is required'}), 400
                if not secret_key:
                    return jsonify({'error': 'Secret Access Key is required'}), 400
                if not bucket:
                    return jsonify({'error': 'Bucket name is required'}), 400
                if not re.match(r'^[a-zA-Z0-9.\-_]{3,63}$', bucket):
                    return jsonify({'error': 'Invalid bucket name format'}), 400

                credentials = {
                    'account_id': account_id,
                    'access_key': access_key,
                    'secret_key': secret_key,
                    'bucket': bucket
                }
            elif provider_type in ['aws', 'wasabi']:
                credentials = {
                    'access_key': request.form.get('access_key'),
                    'secret_key': request.form.get('secret_key'),
                    'bucket': request.form.get('bucket'),
                    'region': request.form.get('region', 'us-east-1')
                }
                if not all(credentials.values()):
                    return jsonify({'error': 'Access key, secret key, bucket and region are required'}), 400
            elif provider_type == 'backblaze':
                key_id = request.form.get('key_id', '').strip()
                application_key = request.form.get('application_key', '').strip()
                bucket_name = request.form.get('bucket_name', '').strip()

                if not key_id:
                    return jsonify({'error': 'Application Key ID is required'}), 400
                if not application_key:
                    return jsonify({'error': 'Application Key is required'}), 400
                if not bucket_name:
                    return jsonify({'error': 'Bucket name is required'}), 400
                if not re.match(r'^[a-z0-9-]{6,50}$', bucket_name):
                    return jsonify({'error': 'Invalid bucket name format for Backblaze B2'}), 400

                credentials = {
                    'application_key_id': key_id,
                    'application_key': application_key,
                    'bucket_name': bucket_name
                }
            elif provider_type == 'wasabi':
                access_key = request.form.get('access_key', '').strip()
                secret_key = request.form.get('secret_key', '').strip()
                bucket = request.form.get('bucket', '').strip()
                region = request.form.get('region', '').strip()

                if not access_key:
                    return jsonify({'error': 'Access Key is required'}), 400
                if not secret_key:
                    return jsonify({'error': 'Secret Key is required'}), 400
                if not bucket:
                    return jsonify({'error': 'Bucket name is required'}), 400
                if not region:
                    return jsonify({'error': 'Region is required'}), 400
                if not re.match(r'^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$', bucket):
                    return jsonify({'error': 'Invalid bucket name format for Wasabi'}), 400

                credentials = {
                    'access_key': access_key,
                    'secret_key': secret_key,
                    'bucket': bucket,
                    'region': region
                }
            elif provider_type == 'gcs':
                credentials_json = request.form.get('credentials_json', '').strip()
                if not credentials_json:
                    return jsonify({'error': 'Service account JSON is required'}), 400
                
                try:
                    # Validate JSON format
                    json.loads(credentials_json)
                    project_id = request.form.get('project_id', '').strip()
                    bucket_name = request.form.get('bucket_name', '').strip()
                    
                    if not project_id:
                        return jsonify({'error': 'Project ID is required'}), 400
                    if not bucket_name:
                        return jsonify({'error': 'Bucket name is required'}), 400
                        
                    credentials = {
                        'project_id': project_id,
                        'bucket_name': bucket_name,
                        'credentials_json': credentials_json
                    }
                except json.JSONDecodeError as e:
                    return jsonify({'error': f'Invalid service account JSON format: {str(e)}'}), 400
            elif provider_type == 'digitalocean':
                access_key = request.form.get('access_key', '').strip()
                secret_key = request.form.get('secret_key', '').strip()
                bucket = request.form.get('bucket', '').strip()
                region = request.form.get('region', '').strip()

                if not access_key:
                    return jsonify({'error': 'Access Key is required'}), 400
                if not secret_key:
                    return jsonify({'error': 'Secret Key is required'}), 400
                if not bucket:
                    return jsonify({'error': 'Bucket name is required'}), 400
                if not region:
                    return jsonify({'error': 'Region is required'}), 400
                if not re.match(r'^[a-z0-9][a-z0-9.-]{2,62}[a-z0-9]$', bucket):
                    return jsonify({'error': 'Invalid bucket name format for DigitalOcean Spaces'}), 400
                if region not in ['nyc3', 'ams3', 'sgp1', 'fra1', 'sfo3']:
                    return jsonify({'error': 'Invalid region for DigitalOcean Spaces'}), 400

                credentials = {
                    'access_key': access_key,
                    'secret_key': secret_key,
                    'bucket': bucket,
                    'region': region
                }
            elif provider_type == 'hetzner':
                access_key = request.form.get('access_key', '').strip()
                secret_key = request.form.get('secret_key', '').strip()
                bucket = request.form.get('bucket', '').strip()
                region = request.form.get('region', '').strip()

                if not access_key:
                    return jsonify({'error': 'Access Key is required'}), 400
                if not secret_key:
                    return jsonify({'error': 'Secret Key is required'}), 400
                if not bucket:
                    return jsonify({'error': 'Bucket name is required'}), 400
                if not region:
                    return jsonify({'error': 'Region is required'}), 400
                if not re.match(r'^[a-z0-9][a-z0-9.-]{2,62}[a-z0-9]$', bucket):
                    return jsonify({'error': 'Invalid bucket name format for Hetzner Storage'}), 400
                if region not in ['nbg1', 'fsn1', 'hel1', 'ash', 'hil', 'sin']:
                    return jsonify({'error': 'Invalid region for Hetzner Storage'}), 400

                credentials = {
                    'access_key': access_key,
                    'secret_key': secret_key,
                    'bucket': bucket,
                    'region': region
                }
            else:
                return jsonify({'error': 'Invalid storage provider selected'}), 400

            # Validate credentials by creating a provider instance
            logger.debug(f"Attempting to create provider instance for {provider_type}")
            provider = get_storage_provider(provider_type, **credentials)
            
            # Test provider by listing files
            logger.debug("Testing provider connection by listing files")
            if hasattr(provider, 'check_connection'):
                provider.check_connection()
            else:
                provider.list_files()
            
            # Store configuration in session
            session['authenticated'] = True
            session['provider_type'] = provider_type
            session['provider_config'] = credential_cipher.encrypt(json.dumps(credentials).encode()).decode()
            
            # Set bucket name based on provider type
            if provider_type == 'gcs':
                session['bucket'] = credentials.get('bucket_name')
            else:
                session['bucket'] = credentials.get('bucket')
            
            logger.info(f"Successfully configured {provider_type} provider with bucket: {session['bucket']}")
            return jsonify({'message': 'Configuration updated successfully'}), 200
            
        except Exception as e:
            logger.error(f"Error configuring storage: {str(e)}", exc_info=True)
            return jsonify({'error': str(e)}), 400
            
    return render_template('configure.html')

@app.route('/upload', methods=['POST'])
@login_required
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    provider = get_current_provider()
    if not provider:
        return jsonify({'error': 'Storage not configured'}), 400
        
    if file:
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({'error': 'Invalid filename'}), 400
        folder = request.form.get('folder', '')
        if folder:
            filename = f"{folder.rstrip('/')}/{filename}"
        try:
            provider.upload_file(file, filename)
            return jsonify({'message': 'File uploaded successfully'}), 200
        except Exception as e:
            logger.error(f"Error uploading file: {str(e)}")
            return jsonify({'error': str(e)}), 500

@app.route('/create_folder', methods=['POST'])
@login_required
def create_folder():
    data = request.get_json(silent=True) or {}
    folder = data.get('folder_name', '')
    if not isinstance(folder, str) or not folder.strip('/') or any(
        part in ('.', '..', '') for part in folder.strip('/').split('/')
    ) or folder.startswith('/') or '\\' in folder:
        return jsonify({'error': 'Invalid folder name'}), 400
    provider = get_current_provider()
    if not provider:
        return jsonify({'error': 'Storage not configured'}), 400
    try:
        provider.upload_file(io.BytesIO(b''), folder.rstrip('/') + '/')
        return jsonify({'message': 'Folder created successfully'}), 200
    except Exception as error:
        logger.error('Error creating folder: %s', error)
        return jsonify({'error': str(error)}), 500

@app.route('/download/<path:filename>')
@login_required
def download(filename):
    provider = get_current_provider()
    if not provider:
        return jsonify({'error': 'Storage not configured'}), 400
        
    try:
        file_obj = provider.download_file(filename)
        return send_file(
            file_obj,
            download_name=os.path.basename(filename),
            as_attachment=True
        )
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/list')
@login_required
def list_files():
    provider = get_current_provider()
    if not provider:
        return jsonify({
            'files': [],
            'message': 'Storage not configured'
        }), 200
        
    try:
        prefix = request.args.get('prefix', '')
        files = provider.list_files(prefix)
        
        file_data = []
        folders = set()
        for file in files:
            try:
                relative_name = file['name'][len(prefix):]
                if not relative_name:
                    continue
                if '/' in relative_name:
                    folder_name = prefix + relative_name.split('/', 1)[0] + '/'
                    if folder_name not in folders:
                        folders.add(folder_name)
                        file_data.append({'name': folder_name, 'size': 0, 'type': 'folder',
                                          'preview_url': None, 'mime_type': None})
                    continue
                mime_type, _ = mimetypes.guess_type(file['name'])
                preview_url = None
                if mime_type and (mime_type.startswith('image/') or 
                                mime_type == 'application/pdf' or 
                                mime_type.startswith('video/')):
                    preview_url = provider.get_file_url(file['name'])
                file_data.append({
                    'name': file['name'],
                    'size': file['size'],
                    'preview_url': preview_url,
                    'mime_type': mime_type,
                    'type': 'file'
                })
            except Exception as e:
                logger.warning(f"Error processing file {file['name']}: {str(e)}")
                continue
                
        return jsonify({'files': file_data}), 200
        
    except Exception as e:
        logger.error(f"Error listing files: {str(e)}")
        return jsonify({
            'error': 'An unexpected error occurred',
            'details': str(e)
        }), 500

@app.route('/delete/<path:filename>', methods=['DELETE'])
@login_required
def delete(filename):
    provider = get_current_provider()
    if not provider:
        return jsonify({'error': 'Storage not configured'}), 400
        
    try:
        provider.delete_file(filename)
        return jsonify({'message': 'File deleted successfully'}), 200
    except Exception as e:
        logger.error(f"Error deleting file: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('configure_storage'))

@app.route('/share/<path:filename>')
@login_required
def share_file(filename):
    provider = get_current_provider()
    if not provider:
        return jsonify({'error': 'Storage not configured'}), 400
        
    try:
        # Generate a URL that expires in 7 days (604800 seconds)
        url = provider.get_file_url(filename, expires_in=604800)
        return jsonify({'url': url}), 200
    except Exception as e:
        logger.error(f"Error generating share link: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host=os.environ.get('HOST', '127.0.0.1'),
            port=int(os.environ.get('PORT', '5001')),
            debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true')
