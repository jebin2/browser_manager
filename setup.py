from setuptools import setup, find_packages

setup(
    name="browser-manager",
    version="0.1.0",
    description="Plug-and-play browser automation with Playwright and Neko Docker",
    author="Jebin Einstein",
    author_email="jebineinstein@gmail.com",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "playwright",
        "requests",
        "psutil",
        "custom_logger @ git+https://github.com/jebin2/custom_logger.git"
    ],
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "browser-manager=browser_manager.browser_manager:main"
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
