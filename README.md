# Cloud Storage Manager

A modern web application for managing files across multiple cloud storage providers with a clean and intuitive interface.

![Storage Manager Screenshot](static/img/screenshot.png)

## Features

- 📁 Intuitive file and folder management
- 🔄 Drag-and-drop file uploads
- 🔗 Secure file sharing with temporary URLs
- 📱 Responsive design for mobile and desktop
- 🎯 Real-time upload progress tracking
- 🗂️ Folder creation and hierarchical navigation
- 🔍 File search and filtering
- 🔒 Secure file handling with AWS S3

## Supported Cloud Providers
![Multi-Cloud Support](static/img/Screenshot-v2.png)

The Cloud Storage Manager supports multiple cloud storage providers, giving you the flexibility to choose the best solution for your needs. Whether you prioritize cost, performance, or specific features, you can seamlessly switch between providers while maintaining a consistent user experience.

### Enterprise-Grade Solutions
- **Amazon S3** ($23/TB/month)
  - Most reliable and feature-rich
  - Multiple storage tiers
  - Advanced analytics and automation
  - Global infrastructure

- **Google Cloud Storage** ($20/TB/month)
  - Integrated with Google services
  - Multiple storage classes
  - Strong consistency
  - 5GB free tier

### Cost-Effective Solutions
- **Cloudflare R2** ($15/TB/month)
  - Zero egress fees
  - Global edge network
  - S3-compatible API
  - 10GB free tier

- **Wasabi** ($6.99/TB/month)
  - No egress fees
  - S3-compatible API
  - 11x9s durability
  - Simple pricing

- **Backblaze B2** ($6/TB/month)
  - Lowest cost
  - Free egress with Cloudflare
  - Simple pricing
  - First 10GB free

- **DigitalOcean Spaces** ($5/TB/month)
  - Simple pricing structure
  - Built-in CDN integration
  - S3-compatible API
  - 250GB included bandwidth

- **Hetzner Storage** ($4/TB/month)
  - European data centers
  - S3-compatible API
  - Automated snapshots
  - Predictable pricing

## Getting Started

### Prerequisites

- Python 3.12 (tested locally and recommended for the AWS environment)
- AWS Account with S3 access

### Installation

1. Clone the repository: 
```bash
git clone https://github.com/rohitg00/s3-file-share-for-free.git
cd s3-file-share-for-free
```
2. Create an isolated environment and install dependencies:
```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

3. Set `FLASK_SECRET_KEY` in `.env` to a random value generated with
   `.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'`.
   Start the same Gunicorn server used on AWS:

```bash
.venv/bin/gunicorn --config gunicorn.conf.py app:app
```

4. Open `http://localhost:5001`. With no `.env` / `PORT` setting, Gunicorn
   defaults to port 8000 for Elastic Beanstalk. For development you can also run
   `.venv/bin/python app.py` (port 5001, debugger disabled unless `FLASK_DEBUG=true`).

Poetry users can use `poetry install --no-root` and
`poetry run gunicorn --config gunicorn.conf.py app:app`.


## Configuration
1. Click "Configure Storage" button
2. Select your preferred storage provider
3. Enter provider-specific credentials:
   - For AWS S3: Access Key ID, Secret Key, Region
   - For Google Cloud: Project ID, Service Account JSON
   - For Cloudflare R2: Account ID, Access Key, Secret Key
   - For other providers: Follow provider-specific instructions

Alternatively, set `S3_BUCKET`, `AWS_DEFAULT_REGION`, and `APP_PASSWORD` in `.env`.
The app then uses the AWS credential chain (a local `AWS_PROFILE`, environment
credentials including `AWS_SESSION_TOKEN`, or an IAM role on AWS). Sign in with
HTTP Basic authentication using `admin` and `APP_PASSWORD`; `APP_USERNAME` changes
the username. In this mode the bucket is set by the environment. Leave
`S3_BUCKET` empty to use the provider configuration form instead.

`FLASK_SECRET_KEY` must be identical across workers and instances. Provider keys
entered through the form are encrypted before being stored in the session;
changing this secret invalidates existing sessions. `.env` and AWS credential
files are excluded from Git and deployment bundles.

### Verification

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
curl --fail http://localhost:5001/health
```

The tests use Moto's simulated S3 service. To verify a real AWS bucket using the
configured login, run:

```bash
.venv/bin/python scripts/check_aws.py --profile YOUR_AWS_PROFILE --bucket YOUR_BUCKET --region YOUR_REGION --round-trip
```

This verifies AWS identity, bucket access, uploads, downloads and a signed share
URL, then deletes only the uniquely named test object it created. Omit
`--round-trip` for an identity and read-only bucket check.

## Usage

### File Upload
- Drag and drop files into the upload area
- Click "Upload" button to select files manually
- Monitor upload progress in real-time

### File Management
- Create folders using the "New Folder" button
- Navigate through folders by clicking
- Delete files/folders using the delete icon
- Download files directly from the interface

### File Sharing
- Generate shareable links with custom expiration
- Copy links to clipboard with one click
- Set access permissions for shared files

## Architecture

The application uses:
- Flask for the backend server
- AWS S3 for file storage
- Modern JavaScript for frontend interactivity
- Bootstrap for responsive design

## Technical Details

- Backend: Python Flask
- Frontend: JavaScript, Tailwind CSS
- Storage: Amazon S3
- File Upload: Chunked upload for large files
- Preview Support: Images, PDFs, Videos
- Security: Server-side AWS credential management

## Security Features

- Secure file handling
- AWS IAM best practices
- Input validation and sanitization
- Temporary URL generation
- Access control implementation

## Deployment on AWS Elastic Beanstalk

The included `Procfile` starts Gunicorn on port 8000, the default port for the
[Elastic Beanstalk Python platform](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/python-configuration-procfile.html).
The bundle includes proxy settings, a 100 MB upload limit, and an unauthenticated
`/health` endpoint for load balancer health checks.

1. Build the source bundle:

   ```bash
   .venv/bin/python scripts/package_aws.py
   ```

   Upload `dist/cloud-storage-aws.zip` as the application source when creating a
   **Python 3.12 on Amazon Linux 2023** Elastic Beanstalk web server environment.
   The archive has application files at its root and excludes local `.env`, AWS
   credentials, virtual environments and tests.

2. Select an Elastic Beanstalk service role and EC2 instance profile in the
   environment wizard. Attach the following bucket-specific policy to the EC2
   instance role, replacing both `YOUR_BUCKET` entries:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
         "Resource": "arn:aws:s3:::YOUR_BUCKET"
       },
       {
         "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
         "Resource": "arn:aws:s3:::YOUR_BUCKET/*"
       }
     ]
   }
   ```

   A bucket encrypted with a customer-managed KMS key also requires access to
   that key. The bucket can remain private; file sharing uses signed URLs.
   Boto3 automatically uses the
   [instance role credentials](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html).

3. Before launching, set these environment properties in Elastic Beanstalk:

   | Property | Value |
   | --- | --- |
   | `FLASK_SECRET_KEY` | A persistent random secret, identical on all instances |
   | `APP_PASSWORD` | The owner's login password |
   | `APP_USERNAME` | `admin`, or your preferred username |
   | `S3_BUCKET` | Your S3 bucket name |
   | `AWS_DEFAULT_REGION` | The bucket's AWS region |

   The bundled configuration sets `PRODUCTION=true` and
   `TRUST_PROXY_HEADERS=true`. Production startup requires a secret key and owner
   password. Keep `PORT` unset to use port 8000. The local `.env` is never uploaded.

4. Configure an HTTPS listener and certificate for the environment, then set
   `FORCE_HTTPS=true`. The app understands the proxy's forwarded HTTPS header.
   If changing `MAX_UPLOAD_MB`, update `.platform/nginx/conf.d/uploads.conf` to
   match and redeploy. Larger transfers can also require proxy/load balancer
   timeout changes; uploads pass through the application server.

5. Check the deployed URL and S3 connection:

   ```bash
   curl --fail https://YOUR_ENVIRONMENT/health
   curl --fail --user admin https://YOUR_ENVIRONMENT/health/storage
   ```

   The second command prompts for the owner password. `/health/storage` must
   return `{"status":"ok"}` to confirm S3 connectivity. Sign in to the website
   and upload/download a small file to finish deployment verification. A local
   test pass or a built archive alone does not verify a live AWS deployment.

## Deployment on Sevalla

### Prerequisites
- A Sevalla account (Sign up at [sevalla.com](https://sevalla.com))
- Your application code pushed to a Git repository
- AWS credentials (Access Key ID and Secret Access Key)

### Deployment Steps

1. **Login to Sevalla Dashboard**
   - Go to [dashboard.sevalla.com](https://dashboard.sevalla.com)
   - Sign in with your credentials

2. **Create New Application**
   - Click on "New Application" 
   - Select your Git repository
   - Choose the branch you want to deploy
   - ✅ Automatic Deployments when commits are pushed to the repository

![S3 File Share GUI Screenshot](https://hostyourgif.live/uploads/1735479021630-s31.gif)

3. **Configure Environment Variables**
   Add the following environment variables:
   ```env
   AWS_ACCESS_KEY_ID=your_access_key_id
   AWS_SECRET_ACCESS_KEY=your_secret_access_key
   AWS_REGION=your_aws_region
   FLASK_SECRET_KEY=your_persistent_random_secret
   APP_PASSWORD=your_owner_password
   PRODUCTION=true
   TRUST_PROXY_HEADERS=true
   ```

![GIF](https://hostyourgif.live/uploads/1735479021720-s32.gif)

4. **Deploy Application**
   - Click "Deploy" to start the deployment process
   - Sevalla will automatically:
     - Install dependencies from requirements.txt
     - Use nix to install dependencies and create docker image
     - Set up the Python environment
     - Start your Flask application

![GIF](https://hostyourgif.live/uploads/1735479021811-s33.gif)

5. **Access Your Application**
   - Once deployed, Sevalla will provide you with a URL
   - Your application will be accessible at `https://your-app-name.sevalla.app`

### Monitoring and Logs

- Access application logs from the Sevalla dashboard
- Monitor application performance metrics
- Set up alerts for application status

For more detailed deployment instructions, visit [Sevalla Documentation](https://docs.sevalla.com)


## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- AWS SDK for Python (Boto3)
- Flask web framework
- Bootstrap CSS framework
- Neon cloud icon design for favicon

## Support

For support, please open an issue in the GitHub repository or contact [email here](ghumare64@gmail.com)

---

Made with ❤️ by Rohit Ghumare
