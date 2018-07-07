from setuptools import setup


setup(
    name="dzonegit",
    version="0.1",
    description="Git hooks to admin DNS zone files in git",
    author="Ondřej Caletka",
    author_email="ondrej@caletka.cz",
    license="MIT",
    py_modules=["dzonegit"],
    setup_requires=['pytest-runner', ],
    tests_require=['pytest', ],
    entry_points={
            "console_scripts": [
                "dzonegit = dzonegit:main",
                "pre-commit = dzonegit:pre_commit",
            ],
    },
)
