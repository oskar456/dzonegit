from setuptools import setup
from pathlib import Path

readme = Path(__file__).with_name("README.rst").read_text()

setup(
    name="dzonegit",
    version="0.15",
    description="Git hooks to manage a repository of DNS zones",
    long_description=readme,
    long_description_content_type="text/x-rst",
    url="https://github.com/oskar456/dzonegit",
    author="Ondřej Caletka",
    author_email="ondrej@caletka.cz",
    license="MIT",
    py_modules=["dzonegit"],
    setup_requires=["pytest-runner"],
    python_requires=">=3.5",
    tests_require=["pytest"],
    entry_points={
            "console_scripts": [
                "dzonegit-pre-commit = dzonegit:pre_commit",
                "dzonegit-pre-receive = dzonegit:pre_receive",
                "dzonegit-post-receive = dzonegit:post_receive",
                "dzonegit-update = dzonegit:update",
                "dzonegit-smudge-serial = dzonegit:smudge_serial",
            ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3 :: Only",
        "Topic :: Software Development :: Version Control :: Git",
        "Topic :: System :: Systems Administration",
    ],

)
