# ============================================================================
# EVCharging System - Kafka Client for Event Streaming
# ============================================================================

import json
import threading
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError
from datetime import datetime
from config import KAFKA_BROKER, KAFKA_TOPICS
from shared.encryption import EncryptionManager  # NEW


class KafkaClient:
    """Kafka client for publishing and consuming events"""

    def __init__(self, component_name):
        self.component_name = component_name
        self.producer = None
        self.consumers = {}
        self.encryption_key = None  # NEW
        self._connect_producer()

    def set_encryption_key(self, password):
        """Set encryption key from password"""
        self.encryption_key = EncryptionManager.generate_key(password)

    def _connect_producer(self):
        """Connect Kafka producer"""
        try:
            self.producer = KafkaProducer(
                bootstrap_servers=[KAFKA_BROKER],
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                request_timeout_ms=5000
            )
        except Exception as e:
            print(f"[{self.component_name}] Kafka producer connection failed: {e}")
            self.producer = None

    def publish_event(self, topic_key, event_type, data):
        """Publish event to Kafka topic"""
        if self.producer is None:
            return

        topic = KAFKA_TOPICS.get(topic_key, "unknown")
        message = {
            "timestamp": datetime.now().isoformat(),
            "component": self.component_name,
            "event_type": event_type,
            "data": data
        }

        try:
            # NEW: Encrypt message if key is set
            if self.encryption_key:
                message_str = json.dumps(message)
                encrypted = EncryptionManager.encrypt(message_str, self.encryption_key)
                self.producer.send(topic, {"encrypted": encrypted})
            else:
                self.producer.send(topic, message)
        except KafkaError as e:
            print(f"[{self.component_name}] Failed to publish: {e}")

    def start_consumer(self, topic_key, consumer_id, callback=None):
        """Start consuming from a Kafka topic in background thread"""
        topic = KAFKA_TOPICS.get(topic_key, "unknown")

        def consume_messages():
            try:
                consumer = KafkaConsumer(
                    topic,
                    bootstrap_servers=[KAFKA_BROKER],
                    group_id=f"{self.component_name}_{consumer_id}",
                    value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                    auto_offset_reset='earliest',
                    consumer_timeout_ms=1000
                )
                self.consumers[consumer_id] = consumer

                for message in consumer:
                    msg_value = message.value
                    
                    # NEW: Decrypt if encrypted
                    if self.encryption_key and isinstance(msg_value, dict) and "encrypted" in msg_value:
                        try:
                            decrypted = EncryptionManager.decrypt(msg_value["encrypted"], self.encryption_key)
                            msg_value = json.loads(decrypted)
                        except Exception as e:
                            print(f"[{self.component_name}] Decryption failed: {e}")
                            continue
                    
                    if callback:
                        callback(msg_value)

            except Exception as e:
                print(f"[{self.component_name}] Consumer {consumer_id} error: {e}")

        thread = threading.Thread(target=consume_messages, daemon=True)
        thread.start()

    def close(self):
        """Close all connections"""
        if self.producer:
            self.producer.close()
        for consumer in self.consumers.values():
            consumer.close()

    @staticmethod
    def log_event(component, event_type, details):
        """Helper to create event dict"""
        return {
            "component": component,
            "event_type": event_type,
            "details": details,
            "timestamp": datetime.now().isoformat()
        }