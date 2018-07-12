from setuptools import setup


setup(
    name="dzonegit",
    version="0.1",
    description="Git hooks to admin DNS zone files in git",
    author="Ondřej Caletka",
    author_email="ondrej@caletka.cz",
    license="MIT",
    py_modules=["dzonegit"],
    setup_requires=["pytest-runner"],
    python_requires=">=3.5",
    tests_require=["pytest"],
    entry_points={
            "console_scripts": [
                "dzonegit = dzonegit:main",
                "dzonegit-pre-commit = dzonegit:pre_commit",
            ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
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
