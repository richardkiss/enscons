# Wheel generation from SCons.
#
# Daniel Holth <dholth@gmail.com>, 2016
#
# The MIT License
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.

from __future__ import unicode_literals, print_function

import os
import sys

# avoid timestamps before 1980 to be friendly to .zip
SOURCE_EPOCH_TGZ = 499162800
SOURCE_EPOCH_ZIP = 499162860

# SCons installs itself in an odd path, under an empty scons/ directory
prefs = []

try:
    import SCons.Script
except ImportError:
    if "SCons" in sys.modules:
        del sys.modules["SCons"]  # or it won't try again
    try:
        # empty scons directory (lowercase) is also a Python 3 namespace package
        import scons

        prefs.extend(scons.__path__)
    except (ImportError, AttributeError):
        # python 2 / pkg_resources method
        try:
            import pkg_resources
        except ImportError:
            pass
        else:
            try:
                d = pkg_resources.get_distribution("scons")
            except pkg_resources.DistributionNotFound:
                pass
            else:
                prefs.append(os.path.join(d.location, "scons"))

sys.path = prefs + sys.path

import SCons.Script

from SCons.Script import Copy, Action, FindInstalledFiles, GetOption, AddOption

from distutils import sysconfig
from collections import defaultdict

from .util import safe_name, to_filename, generate_requirements

import codecs
import distutils.ccompiler, distutils.sysconfig, distutils.unixccompiler
import os.path
import SCons.Node.FS


def get_binary_tag():
    """
    Return most-specific binary extension wheel tag 'interpreter-abi-arch'
    """
    from . import tags

    return str(next(tag for tag in tags.sys_tags() if not "manylinux" in tag.platform))


def get_universal_tag():
    """
    Return 'py2.py3-none-any'
    """
    return "py2.py3-none-any"


def get_abi3_tag():
    """
    Return first abi3 tag, or None if not supported.
    """
    from . import tags

    try:
        return str(next(tag for tag in tags.sys_tags() if "abi3" in tag.abi))
    except StopIteration:
        return get_binary_tag()


def normalize_package(name):
    # XXX encourage project names to start out 'safe'
    return to_filename(safe_name(name))


def egg_info_targets(env):
    """
    Write the minimum .egg-info for pip. Full metadata will go into wheel's .dist-info
    """
    return [
        env.fs.Dir(env["EGG_INFO_PATH"]).File(name)
        for name in ["PKG-INFO", "requires.txt", "entry_points.txt"]
    ]


def develop(env, target=None, source=None):
    """
    Add `scons develop` target to your SConstruct with

    develop = env.Command("#DEVELOP", enscons.egg_info_targets(env), enscons.develop)
    env.Alias("develop", develop)
    """
    import enscons.setup

    enscons_defaults(env)
    enscons.setup.develop(env["EGG_INFO_PREFIX"] or ".")


import setuptools.command.egg_info


class Command(object):
    """Mock object to allow setuptools to write files for us"""

    def __init__(self, distribution):
        self.distribution = distribution

    def write_or_delete_file(self, basename, filename, data, _=True):
        self.data = data


class Distribution(object):
    def __init__(self, metadata):
        self.__dict__ = metadata


def egg_info_builder(target, source, env):
    """
    Minimum egg_info. To be used only by pip to get dependencies.
    """
    # this command helps trick setuptools into doing work for us
    metadata = env["PACKAGE_METADATA"]

    def ensure_property(key, default):
        metadata[key] = metadata.get(key, default)

    ensure_property("install_requires", [])
    ensure_property("extras_require", {})
    ensure_property("entry_points", {})

    command = Command(Distribution(env["PACKAGE_METADATA"]))

    for dnode in env.arg2nodes(target):
        with open(dnode.get_path(), "w") as f:
            if dnode.name == "PKG-INFO":
                f.write("Metadata-Version: 1.1\n")
                f.write("Name: %s\n" % env["PACKAGE_NAME"])
                f.write("Version: %s\n" % env["PACKAGE_VERSION"])
            elif dnode.name == "requires.txt":
                setuptools.command.egg_info.write_requirements(
                    command, dnode.name, "spamalot"
                )
                f.write(command.data)
            elif dnode.name == "entry_points.txt":
                setuptools.command.egg_info.write_entries(
                    command, dnode.name, "spamalot"
                )
                f.write(command.data)


def metadata_builder(target, source, env):
    metadata = env["PACKAGE_METADATA"]
    with codecs.open(target[0].get_path(), mode="w", encoding="utf-8") as f:
        f.write("Metadata-Version: 2.0\n")
        f.write("Name: %s\n" % metadata["name"])
        f.write("Version: %s\n" % metadata["version"])
        # Optional values:
        metadata = defaultdict(lambda: "UNKNOWN", **metadata)
        f.write("Summary: %s\n" % metadata["description"])
        f.write("Home-Page: %s\n" % metadata["url"])
        # XXX expand author to author, author-email with email.utils.parseaddr
        # XXX Author-email can contain both author's name and e-mail
        f.write("Author: %s\n" % metadata["author"])
        f.write("Author-email: %s\n" % metadata["author_email"])
        f.write("License: %s\n" % metadata["license"])
        if not isinstance(metadata["keywords"], list):
            metadata["keywords"] = [metadata["keywords"]]
        f.write("Keywords: %s\n" % " ".join(metadata["keywords"]))
        f.write("Platform: %s\n" % metadata["platform"])
        for classifier in metadata.get("classifiers", []):
            f.write("Classifier: %s\n" % classifier)

        # install_requires is equivalent to extras_require[""][...]
        full_requires = metadata.get("extras_require", {})
        full_requires[""] = full_requires.get("", []) + metadata.get(
            "install_requires", []
        )
        print(full_requires, metadata)
        for requirement in generate_requirements(full_requires):
            f.write("%s: %s\n" % requirement)

        if "description_file" in metadata:
            with codecs.open(
                metadata["description_file"], "r", encoding="utf-8"
            ) as description:
                f.write("\n\n")
                f.write(description.read())


import base64


def urlsafe_b64encode(data):
    """urlsafe_b64encode without padding"""
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def add_manifest(target, source, env):
    """
    Add the wheel manifest.
    """
    import hashlib
    import zipfile

    archive = zipfile.ZipFile(
        target[0].get_path(), "a", compression=zipfile.ZIP_DEFLATED
    )
    lines = []
    for f in archive.namelist():
        data = archive.read(f)
        size = len(data)
        digest = hashlib.sha256(data).digest()
        digest = "sha256=" + (urlsafe_b64encode(digest).decode("ascii"))
        lines.append("%s,%s,%s" % (f.replace(",", ",,"), digest, size))

    record_path = env["DIST_INFO_PATH"].get_path(dir=env["WHEEL_PATH"]) + "/RECORD"
    lines.append(record_path + ",,")
    RECORD = "\n".join(lines)
    archive.writestr(record_path, RECORD)
    archive.close()


def wheelmeta_builder(target, source, env):
    with open(target[0].get_path(), "w") as f:
        f.write(
            """Wheel-Version: 1.0
Generator: enscons (0.0.1)
Root-Is-Purelib: %s
Tag: %s
"""
            % (str(env["ROOT_IS_PURELIB"]).lower(), env["WHEEL_TAG"])
        )


def wheel_metadata(env):
    """Build the wheel metadata."""
    metadata_source = ["pyproject.toml"]
    if env["PACKAGE_METADATA"].get("description_file", ""):
        metadata_source.append(env["PACKAGE_METADATA"].get("description_file"))
    metadata = env.Command(
        env["DIST_INFO_PATH"].File("METADATA"), metadata_source, metadata_builder
    )
    wheelfile = env.Command(
        env["DIST_INFO_PATH"].File("WHEEL"), "pyproject.toml", wheelmeta_builder
    )
    return [metadata, wheelfile]


def init_wheel(env):
    """
    Create a wheel and its metadata using Environment env.
    """
    wheel_filename = (
        "-".join((env["PACKAGE_NAME_SAFE"], env["PACKAGE_VERSION"], env["WHEEL_TAG"]))
        + ".whl"
    )
    wheel_target_dir = env.Dir(env["WHEEL_DIR"])

    # initial # here in path means its relative to top-level sconstruct
    env["WHEEL_PATH"] = env.get("WHEEL_PATH", env.Dir("#build/wheel/"))
    env["DIST_INFO_PATH"] = env["WHEEL_PATH"].Dir(
        env["PACKAGE_NAME_SAFE"] + "-" + env["PACKAGE_VERSION"] + ".dist-info"
    )
    env["WHEEL_DATA_PATH"] = env["WHEEL_PATH"].Dir(
        env["PACKAGE_NAME_SAFE"] + "-" + env["PACKAGE_VERSION"] + ".data"
    )

    whl = env["WHEEL_FILE"] = env.Dir(wheel_target_dir).File(wheel_filename)

    # Write WHEEL and METADATA
    wheelmeta = wheel_metadata(env)

    # Write entry_points.txt if needed
    wheel_entry_points = []
    if env["PACKAGE_METADATA"].get("entry_points"):
        wheel_entry_points = [env["DIST_INFO_PATH"].File("entry_points.txt")]
        env.Command(wheel_entry_points, "pyproject.toml", egg_info_builder)

    targets = wheelmeta + wheel_entry_points

    return targets


def Whl(env, category, source, root=None):
    """
    Copy wheel members into their archive locations.
    category: 'purelib', 'platlib', 'headers', 'data' etc.
    source: files belonging to category
    root: relative to root directory i.e. '.', 'src'
    """
    enscons_defaults(env)

    # Create target the first time this is called
    wheelmeta = []
    try:
        env["WHEEL_FILE"]
    except KeyError:
        wheelmeta = init_wheel(env)

    targets = []
    in_root = ("platlib", "purelib")[env["ROOT_IS_PURELIB"]]
    if category == in_root:
        target_dir = env["WHEEL_PATH"].get_path()
    else:
        target_dir = env["WHEEL_DATA_PATH"].Dir(category).get_path()
    for node in env.arg2nodes(source):
        relpath = os.path.relpath(node.get_path(), root or "")
        args = (os.path.join(target_dir, relpath), node)
        targets.append(env.InstallAs(*args))

    return targets + wheelmeta


def WhlFile(env, target=None, source=None):
    """
    Archive wheel members collected from Whl(...)
    """
    enscons_defaults(env)

    # positional arguments for older enscons
    if target and not source:
        source = target
        target = None

    whl = env.Zip(
        target=target or env.get("WHEEL_FILE"), source=source, ZIPROOT=env["WHEEL_PATH"]
    )

    env.NoClean(whl)
    env.Alias("bdist_wheel", whl)
    env.AddPostAction(whl, Action(add_manifest))
    env.Clean(whl, env["WHEEL_PATH"])

    return whl


def SDist(env, target=None, source=None):
    """
    Call env.Package() with sdist filename inferred from
    env['PACKAGE_METADATA'] etc.
    """
    enscons_defaults(env)

    egg_info = env.Command(egg_info_targets(env), "pyproject.toml", egg_info_builder)
    env.Clean(egg_info, env["EGG_INFO_PATH"])
    env.Alias("egg_info", egg_info)

    pkg_info = env.Command(
        "PKG-INFO", egg_info_targets(env)[0].get_path(), Copy("$TARGET", "$SOURCE")
    )

    src_type = "src_targz"

    # also the root directory name inside the archive
    target_prefix = "-".join((env["PACKAGE_NAME"], env["PACKAGE_VERSION"]))
    if not target:
        target = [os.path.join(env["DIST_BASE"], target_prefix)]

    source = sorted(env.arg2nodes(source, env.fs.Entry))

    sdist = env.PyTar(
        target=target,
        source=source,
        TARPREFIX=target_prefix,
        TARSUFFIX=".tar.gz",
        TARUID=0,
        TARGID=0,
        TARMTIME=SOURCE_EPOCH_TGZ,
    )
    return sdist


def enscons_defaults(env):
    """
    To avoid setting these in generate().
    """
    # once
    if "ROOT_IS_PURELIB" in env:
        return

    try:
        env["ROOT_IS_PURELIB"]
    except KeyError:
        env["ROOT_IS_PURELIB"] = env["WHEEL_TAG"].endswith("none-any")

    env["EGG_INFO_PREFIX"] = GetOption("egg_base")  # pip wants this in a target dir
    env["WHEEL_DIR"] = GetOption("wheel_dir") or "dist"  # target directory for wheel
    env["DIST_BASE"] = GetOption("dist_dir") or "dist"

    env["PACKAGE_NAME"] = env["PACKAGE_METADATA"]["name"]
    env["PACKAGE_NAME_SAFE"] = normalize_package(env["PACKAGE_NAME"])
    env["PACKAGE_VERSION"] = env["PACKAGE_METADATA"]["version"]

    # place egg_info in src_root if defined
    if not env["EGG_INFO_PREFIX"] and env["PACKAGE_METADATA"].get("src_root"):
        env["EGG_INFO_PREFIX"] = env["PACKAGE_METADATA"]["src_root"]

    # Development .egg-info has no version number. Needs to have
    # underscore _ and not hyphen -
    env["EGG_INFO_PATH"] = env["PACKAGE_NAME_SAFE"] + ".egg-info"
    if env["EGG_INFO_PREFIX"]:
        env["EGG_INFO_PATH"] = env.Dir(env["EGG_INFO_PREFIX"]).Dir(env["EGG_INFO_PATH"])


def generate(env):
    """
    Set up enscons in Environment env
    """

    # pure-Python tar
    from . import pytar

    pytar.generate(env)

    if not hasattr(generate, "once"):
        AddOption(
            "--egg-base",
            dest="egg_base",
            type="string",
            nargs=1,
            action="store",
            metavar="DIR",
            help="egg-info target directory",
        )

        AddOption(
            "--wheel-dir",
            dest="wheel_dir",
            type="string",
            nargs=1,
            action="store",
            metavar="DIR",
            help="wheel target directory",
        )

        AddOption(
            "--dist-dir",
            dest="dist_dir",
            type="string",
            nargs=1,
            action="store",
            metavar="DIR",
            help="sdist target directory",
        )

        generate.once = True

    env.AddMethod(Whl)
    env.AddMethod(WhlFile)
    env.AddMethod(SDist)


def exists(env):  # only used if enscons is found on SCons search path
    return True
