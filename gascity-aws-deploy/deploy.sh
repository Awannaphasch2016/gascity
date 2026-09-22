#!/bin/bash
# Complete AWS Deployment Script with Doppler Integration
set -e

echo "🚀 Gas City AWS Deployment (Doppler-Secured)"
echo "=============================================="
echo ""

# Check Doppler
if ! command -v doppler &> /dev/null; then
    echo "❌ Doppler CLI not found. Install it:"
    echo "   macOS: brew install dopplerhq/cli/doppler"
    echo "   Linux: curl -sLf 'https://packages.doppler.com/public/cli/install.sh' | sh"
    exit 1
fi

if ! doppler secrets &> /dev/null; then
    echo "❌ Doppler not configured. Run:"
    echo "   doppler setup"
    exit 1
fi

echo "✅ Doppler configured"

# Load secrets
export $(doppler secrets download --no-file --format env)

# Validate required secrets
required=("AWS_ACCESS_KEY_ID" "AWS_SECRET_ACCESS_KEY" "AWS_REGION" "TELEGRAM_BOT_TOKEN")
for var in "${required[@]}"; do
    if [ -z "${!var}" ]; then
        echo "❌ Missing secret: $var"
        echo "   Set with: doppler secrets set $var='value'"
        exit 1
    fi
done

echo "✅ All secrets found"

# Configuration
AWS_REGION="${AWS_REGION:-us-east-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.medium}"
KEY_NAME="gascity-key"
SG_NAME="gascity-sg"
INSTANCE_NAME="gascity-server"

# Configure AWS CLI
aws configure set aws_access_key_id "$AWS_ACCESS_KEY_ID"
aws configure set aws_secret_access_key "$AWS_SECRET_ACCESS_KEY"
aws configure set region "$AWS_REGION"

# Test AWS
if ! aws sts get-caller-identity &> /dev/null; then
    echo "❌ AWS authentication failed"
    exit 1
fi

echo "✅ AWS credentials validated"

# Create SSH key
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$AWS_REGION" &> /dev/null; then
    echo "🔑 Creating SSH key..."
    aws ec2 create-key-pair \
        --key-name "$KEY_NAME" \
        --region "$AWS_REGION" \
        --query 'KeyMaterial' \
        --output text > "${KEY_NAME}.pem"
    chmod 400 "${KEY_NAME}.pem"
    
    # Store in Doppler
    doppler secrets set SSH_PRIVATE_KEY="$(cat ${KEY_NAME}.pem)" --silent
    echo "✅ SSH key created and stored in Doppler"
else
    echo "✅ SSH key exists"
    if [ ! -f "${KEY_NAME}.pem" ] && [ -n "$SSH_PRIVATE_KEY" ]; then
        echo "$SSH_PRIVATE_KEY" > "${KEY_NAME}.pem"
        chmod 400 "${KEY_NAME}.pem"
        echo "✅ Restored SSH key from Doppler"
    fi
fi

# Create security group
echo "🔒 Creating security group..."
VPC_ID=$(aws ec2 describe-vpcs --region "$AWS_REGION" --filters "Name=isDefault,Values=true" --query 'Vpcs[0].VpcId' --output text)

SG_ID=$(aws ec2 create-security-group \
    --group-name "$SG_NAME" \
    --description "Gas City" \
    --vpc-id "$VPC_ID" \
    --region "$AWS_REGION" \
    --query 'GroupId' \
    --output text 2>/dev/null || \
    aws ec2 describe-security-groups \
        --region "$AWS_REGION" \
        --filters "Name=group-name,Values=$SG_NAME" \
        --query 'SecurityGroups[0].GroupId' \
        --output text)

echo "✅ Security group: $SG_ID"

# Configure firewall
echo "🔓 Configuring firewall..."
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG_ID" --protocol tcp --port 22 --cidr 0.0.0.0/0 2>/dev/null || true
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0 2>/dev/null || true
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0 2>/dev/null || true
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG_ID" --protocol tcp --port 7375 --cidr 0.0.0.0/0 2>/dev/null || true
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG_ID" --protocol tcp --port 8080 --cidr 0.0.0.0/0 2>/dev/null || true

# Get Ubuntu AMI
echo "🔍 Finding Ubuntu AMI..."
AMI_ID=$(aws ec2 describe-images \
    --region "$AWS_REGION" \
    --owners 099720109477 \
    --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text)

echo "✅ AMI: $AMI_ID"

# Generate Doppler service token
echo "🔐 Generating Doppler service token..."
DOPPLER_TOKEN=$(doppler configs tokens create ec2-$(date +%s) --max-age 30d --plain)

# Create user data
cat > user-data.sh <<EOF
#!/bin/bash
set -e

apt-get update
apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# Install Docker Compose
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-\$(uname -s)-\$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Install Doppler
curl -sLf 'https://packages.doppler.com/public/cli/install.sh' | sh

# Configure Doppler
echo "${DOPPLER_TOKEN}" | doppler configure set token --scope /opt/gascity

mkdir -p /opt/gascity
cd /opt/gascity

# Create systemd service
cat > /etc/systemd/system/gascity.service <<'SERVICE'
[Unit]
Description=Gas City
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/gascity
ExecStart=/usr/bin/doppler run -- /usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down

[Install]
WantedBy=multi-user.target
SERVICE

systemctl enable gascity.service
echo "✅ Setup complete!"
EOF

# Launch EC2
echo "🚀 Launching EC2..."
INSTANCE_ID=$(aws ec2 run-instances \
    --region "$AWS_REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --user-data file://user-data.sh \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME}]" \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}' \
    --query 'Instances[0].InstanceId' \
    --output text)

echo "✅ Instance: $INSTANCE_ID"

# Store in Doppler
doppler secrets set EC2_INSTANCE_ID="$INSTANCE_ID" --silent

echo "⏳ Waiting for instance..."
aws ec2 wait instance-running --region "$AWS_REGION" --instance-ids "$INSTANCE_ID"

# Get IP
PUBLIC_IP=$(aws ec2 describe-instances \
    --region "$AWS_REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)

doppler secrets set EC2_PUBLIC_IP="$PUBLIC_IP" --silent

# Clean up
rm -f user-data.sh

echo ""
echo "============================================"
echo "✅ DEPLOYMENT SUCCESSFUL!"
echo "============================================"
echo ""
echo "🌐 Public IP: $PUBLIC_IP"
echo "🔑 SSH Key: ${KEY_NAME}.pem"
echo "📦 Instance: $INSTANCE_ID"
echo ""
echo "Next steps:"
echo ""
echo "1. Wait 5 minutes for setup to complete"
echo ""
echo "2. Upload your files:"
echo "   scp -i ${KEY_NAME}.pem -r ./* ubuntu@${PUBLIC_IP}:/opt/gascity/"
echo ""
echo "3. SSH and start services:"
echo "   ssh -i ${KEY_NAME}.pem ubuntu@${PUBLIC_IP}"
echo "   cd /opt/gascity"
echo "   doppler run -- docker-compose up -d"
echo ""
echo "4. Check logs:"
echo "   docker-compose logs -f"
echo ""
echo "Gas City API: http://${PUBLIC_IP}:7375"
echo ""
