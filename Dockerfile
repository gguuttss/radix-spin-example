# Use Python 3.9 as base image
FROM python:3.9-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies step by step
RUN echo "Installing dependencies..." && \
    pip install --no-cache-dir -r requirements.txt --verbose

RUN echo "Checking installed packages..." && \
    pip list

RUN echo "Checking radix-engine-toolkit installation..." && \
    pip show radix-engine-toolkit

RUN echo "Testing radix-engine-toolkit import..." && \
    python -c "import radix_engine_toolkit; print('radix_engine_toolkit imported successfully')"

# Copy the rest of the application
COPY . .

# Set environment variable for database path
ENV DATABASE_FILE=/app/radix_spin_bot.db

# Create a non-root user
RUN useradd -m botuser && \
    chown -R botuser:botuser /app

# Switch to non-root user
USER botuser

# Initialize database and start bot
CMD ["sh", "-c", "python bot_fixed.py"]