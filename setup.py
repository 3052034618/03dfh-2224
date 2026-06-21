from setuptools import setup, find_packages

setup(
    name="coldchain",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "click>=8.0",
        "openpyxl>=3.0",
    ],
    entry_points={
        "console_scripts": [
            "coldchain=coldchain.cli:main",
        ],
    },
    python_requires=">=3.8",
)
