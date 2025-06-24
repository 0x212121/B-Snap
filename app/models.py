from pydantic import BaseModel

class Camera(BaseModel):
    id: str
    ip: str
    port: int
    username: str
    password: str

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "asset_no": self.asset_no,
            "location": self.location,
            "status": self.status,
            "group_id": self.group_id,  # asumsikan ini FK ke Group
        }