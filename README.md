## Decentralized Social Network Project

A decentralized social networking platform built using Django and Django REST Framework. The application enables users across multiple independent servers to interact through posts, likes, comments, follows, and shared content while maintaining decentralized ownership of data.

## Features

User authentication and profile management
Create, edit, and delete posts
Public and private content visibility
Like and comment on posts
Follow and unfollow users
Inbox system for social interactions
Remote author discovery
Federation between distributed nodes
RESTful API endpoints
Django Admin support

## Tech Stack

Python
Django
Django REST Framework
SQLite / PostgreSQL
HTML, CSS, JavaScript
Bootstrap
Docker (if configured)
Prerequisites
Python 3.11+
pip
virtualenv

## Installation

1. Clone the repository
git clone https://github.com/IFDES/Decentralized-Social-Network-Project.git
cd Decentralized-Social-Network-Project
2. Create a virtual environment
python -m venv venv

## Activate the environment:

macOS/Linux:

source venv/bin/activate

Windows:

venv\Scripts\activate
3. Install dependencies
pip install -r requirements.txt
4. Configure environment variables

Create a .env file and add any required configuration values.

Example:

SECRET_KEY=your-secret-key
DEBUG=True
5. Apply migrations
python manage.py migrate
6. Create an administrator account
python manage.py createsuperuser
7. Start the development server
python manage.py runserver

Visit:

http://127.0.0.1:8000/
Running Tests
python manage.py test
## License

* Choose an OSI approved license, name it here, and copy the license text to a file called `LICENSE`.

## Copyright

The authors claiming copyright, if they wish to be known, can list their names here...

* <a target="_blank" href="https://icons8.com/icon/86527/home">Home</a> icon by <a target="_blank" href="https://icons8.com">Icons8</a>
* <a target="_blank" href="https://icons8.com/icon/15263/profile">Profile</a> icon by <a target="_blank" href="https://icons8.com">Icons8</a>
