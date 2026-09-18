import pytest
from fastapi.testclient import TestClient
from app.main import create_app


class ActivePersonQueue:
    def is_active(self, kind, project_id):
        return kind == 'person_control'

    def submit(self, *args):
        return False

    def shutdown(self):
        pass


@pytest.mark.parametrize('route', ['reference-media', 'reference-video'])
def test_person_control_job_blocks_reference_replacement_before_reading_upload(tmp_path, route):
    client = TestClient(create_app(data_dir=tmp_path, local_compute_queue=ActivePersonQueue()))
    project = client.post('/api/projects', json={'name': '人物控制测试'}).json()
    response = client.put(f'/api/projects/{project["id"]}/{route}', files={'file': ('clip.mp4', b'video', 'video/mp4')})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'person_control_in_progress'
