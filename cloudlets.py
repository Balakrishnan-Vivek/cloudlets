
from __future__ import with_statement

import os
import re
import sys
import tarfile
import subprocess
import tempfile
import shutil
import simplejson as simplejson

import js
import jsonschema
from ejs import EJSTemplate

def filter_path(path, include, exclude):
    if not hasattr(include, "__iter__"):
        include = [include]
    if not hasattr(exclude, "__iter__"):
        exclude = [exclude]
    def match_filters(path, filters):
        return any([f.match(path) if hasattr(f, "match") else f == path for f in filters])
    return match_filters(path, include) or not match_filters(path, exclude)


class Manifest(dict):
    """A dictionary holding an image's metadata"""

    specs = {
        "type": "object",
        "properties": {
            "name"          : {"optional": False, "type": "string", "description": "Canonical name of the image. example: org.dotcloud.debian-lenny-i386"},
            "description"   : {"optional": True,  "type": "string", "description": "User-readable description of the image"},
            "arch"          : {"optional": True,  "type": "string", "description": "Hardware architecture. example: i386"},
            "args"          : {"optional": True,  "type": "object", "description": "List of accepted user-specified configuration arguments", "default": {}},
            "templates"     : {"optional": True,  "type": "array", "description": "List of files which are templates", "default": []},
            "persistent"    : {"optional": True,  "type": "array", "description": "List of files or directories holding persistent data", "default": []},
            "ignore"        : {"optional": True,  "type": "array", "description": "List of patterns for files whose changes should be ignored"}
        }
    }

    def validate(self):
        """Validate contents of the manifest against the cloudlets spec"""
        print "validating %s" % self
        jsonschema.validate(dict(self), self.specs)

    def __init__(self, *args, **kw):
        dict.__init__(self, *args, **kw)
        self.validate()

class Image(object):

    def __init__(self, path):
        self.path = os.path.abspath(path)

    def tar(self):
        tar = tarfile.open("", mode="w|", fileobj=sys.stdout)
        for path in self.get_files(exclude=map(re.compile, self.manifest["ignore"])):
            tar.add(self.path + path, path, recursive=False)

    def get_files(self, include=[], exclude=[]):
        for (basepath, dpaths, fpaths) in os.walk(self.path, topdown=True):
            chrooted_basepath = "/" if basepath == self.path else basepath.replace(self.path, "")
            for subpath in dpaths + fpaths:
                path = os.path.join(chrooted_basepath, subpath)
                if filter_path(path, include, exclude):
                    yield path
    files = property(get_files)

    def get_fs_templates(self):
        return list(self.get_files(exclude=re.compile(".*"), include=self.manifest.get("templates", [])))
    fs_templates = property(get_fs_templates)

    def get_fs_ignore(self):
        return list(self.get_files(exclude=re.compile(".*"), include=map(re.compile, self.manifest.get("ignore", []))))
    fs_ignore = property(get_fs_ignore)

    def get_fs_persistent(self):
        return list(self.get_files(exclude=re.compile(".*"), include=self.manifest.get("persistent", [])))
    fs_persistent = property(get_fs_persistent)

    def get_fs_other(self):
        return list(self.get_files(exclude=self.manifest.get("templates") + map(re.compile, self.manifest.get("ignore")) + self.manifest.get("persistent")))
    fs_other = property(get_fs_other)

    def get_cloudletdir(self):
        return os.path.join(self.path, ".cloudlet")
    cloudletdir = property(get_cloudletdir)

    def get_manifestfile(self):
        return os.path.join(self.cloudletdir, "manifest")
    manifestfile = property(get_manifestfile)

    def get_manifest(self):
        if os.path.exists(self.manifestfile):
            return Manifest(simplejson.loads(file(self.manifestfile).read()))
        return {}
    manifest = property(get_manifest)

    def get_config_schema(self):
        schema_skeleton =  {
            "dns": {
                "nameservers": {"type": "array"}
            },
            "ip": {
                "interfaces": {"type": "array"}
            },
            "args": self.args_schema
        }
        return {
            "type": "object",
            "properties": dict([(key, {"type": "object", "properties": section}) for (key, section) in schema_skeleton.items()])
        }
    config_schema = property(get_config_schema)

    def get_args_schema(self):
        return self.manifest.get("args", {})
    args_schema = property(get_args_schema)

    def validate_config(self, config):
        jsonschema.validate(config, self.config_schema)

    def get_config_file(self):
        return os.path.join(self.cloudletdir, "applied_config")
    config_file = property(get_config_file)

    def get_config(self):
        if not os.path.exists(self.config_file):
            return None
        return simplejson.loads(file(self.config_file).read())

    def set_config(self, config):
        if self.config:
            raise ValueError("Already configured: %s" % self.config)
        file(self.config_file, "w").write("")
        self.validate_config(config)
        for template in self.manifest.get("templates", []):
            print "Applying template %s with %s" % (template, config)
            EJSTemplate(self.path + template).apply(self.path + template, config)
        file(self.config_file, "w").write(simplejson.dumps(config, indent=1))

    config = property(get_config, set_config)
