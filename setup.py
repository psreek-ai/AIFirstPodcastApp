from setuptools import setup, find_packages

setup(
    name='aethercast',
    version='0.1.0',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        # Add any top-level dependencies here
    ],
    entry_points={
        'console_scripts': [
            # Add any command-line scripts here
        ],
    },
)
