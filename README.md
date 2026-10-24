# 🍱 FoodShare — Share Food, Spread Hope

### A Community-Driven Platform to Reduce Food Waste and Fight Hunger

<p align="center">
  <a href="https://foodshare-81o9.onrender.com/about">
    <img src="https://img.shields.io/badge/Live%20Demo-Visit%20FoodShare-2ea44f?style=for-the-badge&logo=render&logoColor=white" alt="Live Demo"/>
  </a>
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Flask-000000?style=for-the-badge&logo=flask&logoColor=white" alt="Flask"/>
  <img src="https://img.shields.io/badge/MySQL-4479A1?style=for-the-badge&logo=mysql&logoColor=white" alt="MySQL"/>
  <img src="https://img.shields.io/badge/Deployed%20on-Render-46E3B7?style=for-the-badge&logo=render&logoColor=black" alt="Render"/>
</p>

---

## 🌍 About the Project

**FoodShare** is a web-based food-sharing platform built to address two important real-world challenges: food waste and hunger.

Every day, edible food goes unused while many people struggle to access sufficient meals. FoodShare aims to bridge this gap by providing a digital platform where surplus food can be shared with people and communities that need it.

The project focuses on making food sharing more accessible, encouraging responsible consumption, and using technology to create a positive social impact.

🔗 **Live Website:** https://foodshare-81o9.onrender.com/about

## 🎯 Problem Statement

- 🍽️ Edible food is frequently wasted by households, restaurants, and food businesses.
- 🤝 People and communities in need may struggle to find available surplus food.
- 📍 Connecting food donors with potential recipients can be difficult without a centralized platform.
- 🌱 Food waste contributes to unnecessary resource consumption and environmental impact.

### 💡 Our Solution

FoodShare aims to simplify food sharing through a centralized web platform that helps connect surplus food with people who can benefit from it.

## ✨ Key Objectives

- ♻️ Reduce avoidable food waste.
- 🥗 Encourage responsible food consumption.
- 🤝 Promote community participation and food sharing.
- 🌍 Support a more sustainable and socially responsible society.
- 💻 Use web technology to address a real-world problem.

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Backend programming |
| Flask | Web application framework |
| MySQL | Relational database |
| HTML5 | Web page structure |
| CSS3 | Styling and layout |
| JavaScript | Client-side interactivity |
| Bootstrap | Responsive user interface |
| Git & GitHub | Version control and source code management |
| Render | Application deployment and hosting |

## ⚙️ How It Works

The FoodShare concept follows a simple process:

1. **Share:** A food donor provides details about available surplus food.
2. **Discover:** Potential recipients explore available food-sharing opportunities.
3. **Connect:** Donors and recipients coordinate food collection or distribution.
4. **Redistribute:** Suitable surplus food reaches people who can use it.
5. **Make an Impact:** Communities help reduce waste and promote sustainability.

*The exact workflow depends on the functionality implemented in the current version of the application.*

## 🚀 Live Demo

Explore the deployed application:

👉 **[Open FoodShare Website](https://foodshare-81o9.onrender.com/about)**

The project is hosted on Render, with MySQL used for database functionality.

> Note: The first request after a period of inactivity may take longer if the hosting service has put the application to sleep.

## 💻 Run Locally

Follow these steps to set up the project on your computer.

### Prerequisites

Make sure you have installed:

- Python 3
- MySQL Server
- Git
- A code editor, such as Visual Studio Code

### 1. Clone the Repository

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd <YOUR_PROJECT_FOLDER>
```

Replace the placeholders with your actual GitHub repository URL and project folder name.

### 2. Create a Virtual Environment

```bash
python -m venv venv
```

Activate the environment.

**Windows:**

```bash
venv\Scripts\activate
```

**macOS / Linux:**

```bash
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

If your repository does not contain a `requirements.txt` file, install the packages listed in your project's dependency configuration.

### 4. Configure the Database

Create a MySQL database for the application:

```sql
CREATE DATABASE foodshare;
```

Configure your database connection using environment variables supported by your application's code. Keep database passwords, credentials, and SSL certificate paths out of public source control.

### 5. Run the Application

Use the entry-point file configured in your project. For example, if the main file is `app.py`:

```bash
python app.py
```

Open the local URL displayed in your terminal, commonly `http://127.0.0.1:5000/`.

## 🔐 Security Best Practices

- Store credentials in environment variables.
- Never commit `.env` files, database passwords, or private SSL certificates.
- Validate user input on the server.
- Use secure password handling wherever authentication is implemented.
- Configure production settings separately from local development settings.

## 🌱 Future Enhancements

Potential improvements for future versions include:

- 📍 Location-based discovery of nearby food donations.
- 🔔 Notifications for new food-sharing opportunities.
- 📊 Donation history and community impact statistics.
- 🛡️ Donor and recipient verification.
- 📱 Improved mobile experience.
- 🗺️ Interactive maps for pickup locations.
- 👥 Expanded community participation and collaboration.

These are future possibilities, not a claim that every feature is currently implemented.

## 🤝 Contributing

Contributions and suggestions are welcome!

1. Fork the repository.
2. Create a feature branch.
3. Make your changes.
4. Test your implementation.
5. Submit a pull request with a clear description of your changes.

## 👨‍💻 Developer

**Vivek Chaudhari**

B.Tech Student | Aspiring Software Developer

Interested in Python, Flask, SQL, full-stack web development, and building technology-driven solutions to real-world problems.

- 🌐 **Live Project:** [FoodShare](https://foodshare-81o9.onrender.com/about)
- 💻 **GitHub:** [https://github.com/chaudhari2004)

## 📄 License

A license has not yet been specified. Add a `LICENSE` file to the repository if you intend to distribute the project under an open-source license.

---

<p align="center">
  <b>FoodShare — Share More. Waste Less. Care More. 🌱</b>
</p>

*Built with ❤️ to encourage food sharing and create a more sustainable future.*
