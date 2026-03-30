FROM node:20-alpine

# Set working directory
WORKDIR /app

# Copy package manifests first for layer caching
COPY package*.json ./

# Install only production dependencies
RUN npm ci --only=production

# Copy application source
COPY src/ ./src/

# Expose service port
EXPOSE 3000

# Run the service
CMD ["node", "src/index.js"]
