CREATE DATABASE foodshare_db;

USE foodshare_db;

USE foodshare_db;

CREATE TABLE users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    phone VARCHAR(20),
    role ENUM('donor', 'recipient', 'admin')
        NOT NULL DEFAULT 'recipient',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE food_listings (
    food_id INT AUTO_INCREMENT PRIMARY KEY,
    donor_id INT NOT NULL,
    food_name VARCHAR(150) NOT NULL,
    description TEXT,
    quantity INT NOT NULL,
    quantity_unit VARCHAR(30) NOT NULL DEFAULT 'servings',
    pickup_location VARCHAR(255) NOT NULL,
    expiry_time DATETIME,
    status ENUM('available', 'reserved', 'collected', 'expired')
        NOT NULL DEFAULT 'available',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (donor_id) REFERENCES users(user_id)
);




CREATE TABLE food_requests (
    request_id INT AUTO_INCREMENT PRIMARY KEY,
    food_id INT NOT NULL,
    recipient_id INT NOT NULL,
    requested_quantity INT NOT NULL DEFAULT 1,
    status ENUM('pending', 'approved', 'rejected', 'completed')
        NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (food_id) REFERENCES food_listings(food_id),
    FOREIGN KEY (recipient_id) REFERENCES users(user_id)
);

show tables;

SELECT CURRENT_USER(), VERSION();

SELECT CURRENT_USER();

CREATE USER 'foodshare_app'@'localhost'
IDENTIFIED BY 'ChooseYourStrongPassword123!';

GRANT ALL PRIVILEGES ON foodshare_db.*
TO 'foodshare_app'@'localhost';


select * from food_listings;
USE foodshare_db;
select * from users;
show tables;
select * from food_listings;

ALTER TABLE food_listings
ADD COLUMN latitude DECIMAL(10, 7) NULL,
ADD COLUMN longitude DECIMAL(10, 7) NULL;
 select * from food_listings;
 
 USE foodshare_db;

UPDATE food_listings
SET status = 'expired'
WHERE status = 'available'
  AND expiry_time <= NOW();

SELECT food_id, food_name, expiry_time, status
FROM food_listings
ORDER BY food_id DESC;

USE foodshare_db;

SET SQL_SAFE_UPDATES = 0;

UPDATE food_listings
SET status = 'expired'
WHERE status = 'available'
  AND expiry_time <= NOW();

SELECT food_id, food_name, expiry_time, status
FROM food_listings
ORDER BY food_id DESC;
select * from users;

ALTER TABLE food_listings
ADD COLUMN donation_type ENUM('free', 'paid')
NOT NULL DEFAULT 'free',
ADD COLUMN price_per_unit DECIMAL(10,2) NULL;


 select *   from food_listings;

INSERT INTO food_listings (
    donor_id,
    food_name,
    description,
    quantity,
    quantity_unit,
    pickup_location,
    latitude,
    longitude,
    expiry_time,
    status,
    image_filename,
    donation_type,
    price_per_unit
)
VALUES (
    1,
    'Veg Rice',
    'Fresh vegetable rice',
    10,
    'plates',
    'Pune',
    NULL,
    NULL,
    '2026-12-31 18:00:00',
    'available',
    NULL,
    'paid',
    20.00
);

ALTER TABLE food_listings
ADD COLUMN category VARCHAR(80) NULL AFTER food_name;

CREATE TABLE IF NOT EXISTS food_favourites (
    user_id INT NOT NULL,
    food_id INT NOT NULL,
    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (user_id, food_id),

    CONSTRAINT fk_favourite_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_favourite_food
        FOREIGN KEY (food_id)
        REFERENCES food_listings(food_id)
        ON DELETE CASCADE
);
select * from food_listings;

DESCRIBE food_favourites;



USE foodshare;

CREATE TABLE IF NOT EXISTS food_reviews (
    review_id INT AUTO_INCREMENT PRIMARY KEY,
    food_id INT NOT NULL,
    reviewer_id INT NOT NULL,
    reviewee_id INT NOT NULL,
    rating TINYINT NOT NULL,
    review_text VARCHAR(1000) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_review_rating
        CHECK (rating BETWEEN 1 AND 5),

    CONSTRAINT uq_food_reviewer
        UNIQUE (food_id, reviewer_id),

    CONSTRAINT fk_review_food
        FOREIGN KEY (food_id)
        REFERENCES food_listings(food_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_review_reviewer
        FOREIGN KEY (reviewer_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_review_reviewee
        FOREIGN KEY (reviewee_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE
);
DESCRIBE food_reviews;
SELECT
    food_id,
    reviewer_id,
    reviewee_id,
    rating,
    review_text,
    created_at
FROM food_reviews
ORDER BY created_at DESC;

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id INT AUTO_INCREMENT PRIMARY KEY,
    food_id INT NOT NULL,
    donor_id INT NOT NULL,
    recipient_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uq_food_recipient (food_id, recipient_id),

    FOREIGN KEY (food_id)
        REFERENCES food_listings(food_id)
        ON DELETE CASCADE,

    FOREIGN KEY (donor_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    FOREIGN KEY (recipient_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    message_id INT AUTO_INCREMENT PRIMARY KEY,
    conversation_id INT NOT NULL,
    sender_id INT NOT NULL,
    message_text VARCHAR(2000) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_read BOOLEAN DEFAULT FALSE,

    FOREIGN KEY (conversation_id)
        REFERENCES conversations(conversation_id)
        ON DELETE CASCADE,

    FOREIGN KEY (sender_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE
);

ALTER TABLE messages
ADD COLUMN image_filename VARCHAR(255) NULL;

SHOW COLUMNS FROM messages;
select * FROM messages;
SHOW COLUMNS FROM messages LIKE 'image_filename';
ALTER TABLE food_listings
ADD COLUMN remaining_quantity DECIMAL(10,2) NULL;

UPDATE food_listings
SET remaining_quantity = quantity
WHERE remaining_quantity IS NULL;


USE foodshare_db;

CREATE TABLE IF NOT EXISTS notifications (
    notification_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    title VARCHAR(150) NOT NULL,
    message VARCHAR(500) NOT NULL,
    notification_type VARCHAR(50) NOT NULL,
    related_url VARCHAR(500) NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_notifications_user_read
        (user_id, is_read, created_at),
    CONSTRAINT fk_notifications_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS food_expiry_alerts (
    alert_id INT AUTO_INCREMENT PRIMARY KEY,
    food_id INT NOT NULL,
    alert_type VARCHAR(30) NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_food_expiry_alert (food_id, alert_type),
    CONSTRAINT fk_expiry_alert_food
        FOREIGN KEY (food_id)
        REFERENCES food_listings(food_id)
        ON DELETE CASCADE
);


USE foodshare_db;

CREATE TABLE IF NOT EXISTS notifications (
    notification_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    title VARCHAR(150) NOT NULL,
    message VARCHAR(500) NOT NULL,
    notification_type VARCHAR(50) NOT NULL,
    related_url VARCHAR(500) NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_notifications_user_read
        (user_id, is_read, created_at),

    CONSTRAINT fk_notifications_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS password_reset_otps (
    email VARCHAR(254) PRIMARY KEY,
    otp_hash VARCHAR(255) NOT NULL,
    expires_at DATETIME NOT NULL,
    attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
    last_sent_at DATETIME NOT NULL,
    verified_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

SHOW TABLES;


SHOW COLUMNS FROM users;
UPDATE users
SET role = 'admin'
WHERE email = 'adminfood@gmail.com';
