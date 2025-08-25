#!/bin/bash

# Start Ollama server in the background
/bin/ollama serve &
pid=$!

# Wait for Ollama to be ready
echo "Waiting for Ollama to start..."
until ollama list > /dev/null 2>&1; do
  sleep 2
done

# Update the Modelfile's env variables
apt-get update && apt-get install -y gettext
mkdir -p /modelfiles
envsubst < /templates/Modelfile.template > /modelfiles/Modelfile

# Build the custom model if not already present
if ! ollama list | grep -q "$OLLAMA_MODEL_NAME"; then
  echo "Building model: $OLLAMA_MODEL_NAME"
  ollama create $OLLAMA_MODEL_NAME -f /modelfiles/Modelfile
else
  echo "Model $OLLAMA_MODEL_NAME already exists."
fi

# Wait for Ollama server to finish (keep container running)
wait $pid
