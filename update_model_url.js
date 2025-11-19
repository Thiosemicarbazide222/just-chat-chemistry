/**
 * Usage:
 *   podman cp update_model_url.js just-chat-mongodb-for-agents:/tmp/update_model_url.js
 *   podman exec just-chat-mongodb-for-agents mongosh \
 *     "mongodb://genie:super-secret-password@localhost:27017/huggingchat?authSource=admin" \
 *     /tmp/update_model_url.js
 */

db.assistants.updateMany(
  {},
  {
    $set: {
      "model.baseURL": "http://just-chat-agents:8091/v1",
    },
  }
);