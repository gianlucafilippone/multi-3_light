from setuptools import find_packages, setup

package_name = 'multi3_executor'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Gianluca Filippone',
    maintainer_email='gianlucafilippone@gssi.it',
    description='Multi-3 Executor Component',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'executor=multi3_executor.nodes.executor:main'
        ],
    },
)
