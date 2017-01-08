from setuptools import setup, find_packages

setup(
    name='smscliclient',
    version='0.1.dev',
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'smscli-client = smscliclient.smscliclient:main'
        ]
    },
    install_requires=['urwid'],
    extras_require={
        'Notifications': ['gobject']
    },
    license='',      # TODO
    url='',          # TODO
    author='m5tt',
    author_email='arks36@protonmail.com'
)
