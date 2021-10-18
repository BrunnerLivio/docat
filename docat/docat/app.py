"""
docat
~~~~~

Host your docs. Simple. Versioned. Fancy.

:copyright: (c) 2019 by docat, https://github.com/docat-org/docat
:license: MIT, see LICENSE for more details.
"""
import os
import secrets
from http import HTTPStatus
from pathlib import Path

from flask import Flask, request, send_from_directory
from tinydb import Query, TinyDB
from werkzeug.utils import secure_filename

from docat.utils import UPLOAD_FOLDER, calculate_token, create_nginx_config, create_symlink, extract_archive, remove_docs

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = Path(os.getenv("DOCAT_DOC_PATH", UPLOAD_FOLDER))
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100M
app.db = TinyDB("db.json")


@app.route("/api/<project>/<version>", methods=["POST"])
def upload(project, version):
    if "file" not in request.files:
        return {"message": "No file part in the request"}, HTTPStatus.BAD_REQUEST

    uploaded_file = request.files["file"]
    if uploaded_file.filename == "":
        return {"message": "No file selected for uploading"}, HTTPStatus.BAD_REQUEST

    project_base_path = app.config["UPLOAD_FOLDER"] / project
    base_path = project_base_path / version
    target_file = base_path / secure_filename(uploaded_file.filename)

    if base_path.exists():
        token = request.headers.get("Docat-Api-Key")
        result = check_token_for_project(token, project)
        if result is True:
            remove_docs(project, version)
        else:
            return result

    # ensure directory for the uploaded doc exists
    base_path.mkdir(parents=True, exist_ok=True)

    # save the uploaded documentation
    uploaded_file.save(str(target_file))
    extract_archive(target_file, base_path)

    create_nginx_config(project, project_base_path)

    return {"message": "File successfully uploaded"}, HTTPStatus.CREATED


@app.route("/api/<project>/<version>/tags/<new_tag>", methods=["PUT"])
def tag(project, version, new_tag):
    source = version
    destination = app.config["UPLOAD_FOLDER"] / project / new_tag

    if create_symlink(source, destination):
        return (
            {"message": f"Tag {new_tag} -> {version} successfully created"},
            HTTPStatus.CREATED,
        )
    else:
        return (
            {"message": f"Tag {new_tag} would overwrite an existing version!"},
            HTTPStatus.CONFLICT,
        )


@app.route("/api/<project>/claim", methods=["GET"])
def claim(project):
    Project = Query()
    table = app.db.table("claims")
    result = table.search(Project.name == project)
    if result:
        return (
            {"message": f"Project {project} is already claimed!"},
            HTTPStatus.CONFLICT,
        )

    token = secrets.token_hex(16)
    salt = os.urandom(32)
    token_hash = calculate_token(token, salt)
    table.insert({"name": project, "token": token_hash, "salt": salt.hex()})

    return {"message": f"Project {project} successfully claimed", "token": token}, HTTPStatus.CREATED


@app.route("/api/<project>/<version>", methods=["DELETE"])
def delete(project, version):
    token = request.headers.get("Docat-Api-Key")

    result = check_token_for_project(token, project)
    if result is True:
        message = remove_docs(project, version)
        if message:
            return ({"message": message}, HTTPStatus.NOT_FOUND)
        else:
            return (
                {"message": f"Successfully deleted version '{version}'"},
                HTTPStatus.OK,
            )
    else:
        return result


def check_token_for_project(token, project):
    Project = Query()
    table = app.db.table("claims")
    result = table.search(Project.name == project)

    if result and token:
        token_hash = calculate_token(token, bytes.fromhex(result[0]["salt"]))
        if result[0]["token"] == token_hash:
            return True
        else:
            return ({"message": f"Docat-Api-Key token is not valid for {project}"}, HTTPStatus.UNAUTHORIZED)
    else:
        return ({"message": f"Please provide a header with a valid Docat-Api-Key token for {project}"}, HTTPStatus.UNAUTHORIZED)


# serve_local_docs for local testing without a nginx
if os.environ.get("DOCAT_SERVE_FILES"):

    @app.route("/doc/<path:path>")
    def serve_local_docs(path):
        return send_from_directory(app.config["UPLOAD_FOLDER"], path)
