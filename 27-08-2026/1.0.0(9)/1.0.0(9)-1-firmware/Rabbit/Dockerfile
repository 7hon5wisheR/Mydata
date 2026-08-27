FROM rabbitmq:3-management
RUN apt-get update && apt-get install -y inotify-tools && apt-get clean
RUN mkdir -p /etc/rabbitmq/ssl
COPY rabbitmq/cacert.pem /etc/rabbitmq/ssl/cacert.pem
COPY rabbitmq/cert.pem /etc/rabbitmq/ssl/cert.pem
COPY rabbitmq/key.pem /etc/rabbitmq/ssl/key.pem
COPY rabbitmq/rabbitmq.conf /etc/rabbitmq/rabbitmq.conf
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
RUN chown -R rabbitmq:rabbitmq /etc/rabbitmq/ssl && \
    chmod 640 /etc/rabbitmq/ssl/*.pem && \
    chmod 600 /etc/rabbitmq/ssl/key.pem && \
    chmod 644 /etc/rabbitmq/rabbitmq.conf
RUN rabbitmq-plugins enable rabbitmq_mqtt rabbitmq_web_mqtt
EXPOSE 5672 15672 8883 1883 15675
ENTRYPOINT ["/entrypoint.sh"]
CMD ["rabbitmq-server"]