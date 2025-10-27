#!/bin/sh
# This script is used to substitute environment variables in the Nginx configuration template.

# The list of variables to be substituted
export DOLLAR_VARS='${NGINX_BACKEND_URL}'

# Substitute the variables in the template file and output to the final config file
envsubst "$DOLLAR_VARS" < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf

# Start Nginx in the foreground
nginx -g 'daemon off;'
