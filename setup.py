import os
from setuptools import setup


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


setup(
    name='gpu-monitor',
    version='0.1.0',
    author='Nichita Utiu',
    author_email='nikita.utiu@gmail.com',
    description='Simple script to monitor gpu usage remotely or locally',
    license='GPLv3',
    keywords='script utility gpu monitor',
    url='https://github.com/nikitautiu/gpu-monitor/',

    packages=['gpu_monitor'],
    entry_points={
        'console_scripts': [
            'gpu-monitor = gpu_monitor:main',
        ],
    },

    long_description=read('README.md'),
    classifiers=[
        'Topic :: Utilities',
        'License :: OSI Approved :: GPL License',
    ],
)
