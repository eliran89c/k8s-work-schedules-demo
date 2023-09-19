FROM python:3.11-alpine

WORKDIR /usr/src/app

COPY operator/requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY operator/ .

# By default, listen on all namespaces and expose port 5000 for liveness probe
CMD [ "kopf", "run", "workschedule.py", "-A", "--liveness", "http://0.0.0.0:5000" ]