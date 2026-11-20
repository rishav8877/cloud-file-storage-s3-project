from app import app
import os

def check_aws_credentials():
    if not app.config['S3_BUCKET']:
        print("Configure a storage provider through the web interface.")
    else:
        print("Using the configured S3 bucket with the AWS credential chain.")

if __name__ == "__main__":
    check_aws_credentials()
    app.run(host=os.environ.get('HOST', '127.0.0.1'),
            port=int(os.environ.get('PORT', '5001')),
            debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true')
