#!/bin/bash

EC2_HOST="100.51.127.130"
EC2_USER="ec2-user"
EC2_KEY="cs-chatbot-key.pem"

SERVICE="${1:-all}"

ssh -i "$EC2_KEY" "${EC2_USER}@${EC2_HOST}" << EOF
    if [ "$SERVICE" = "all" ]; then
        docker-compose logs --tail=100 -f
    else
        docker-compose logs --tail=100 -f $SERVICE
    fi
EOF
