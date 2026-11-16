def move(client, pid, type_, qty, **kw):
    return client.post("/inventory/movements", json={"product_id": pid, "type": type_, "quantity": qty, **kw})
