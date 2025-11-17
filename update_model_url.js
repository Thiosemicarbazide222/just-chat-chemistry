db.assistants.updateMany(
    {},
    {
      $set: {
        "model.baseURL": "http://search-logger:8099/v1"
      }
    }
  );
  