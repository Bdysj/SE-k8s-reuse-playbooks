# ratings, adapted for the architecture feasibility check.
#
# Deviations from sources/robot-shop/ratings/Dockerfile, all recorded:
#
# 1. The composer stage is pinned. The original writes COPY --from=composer,
#    which resolves to whatever :latest happens to be at build time. A moving
#    dependency cannot appear in a baseline that Section 3.5.1 requires to be
#    pinned, and it would also differ between the two architecture builds.
#
# 2. The Instana AutoProfile ini line is dropped. It enables a beta profiler that
#    is not installed in this image, it is unrelated to the repair families under
#    study, and leaving it in would add a per-request cost to only one group if it
#    behaved differently across architectures.
#
# 3. The unzip package is dropped. This is forced, not a choice. The image is
#    Debian 11 (bullseye), whose security packages have moved to
#    archive.debian.org, so the apt index still offers unzip 6.0-26+deb11u2 while
#    the pool now carries only deb12 and deb13 builds. The download returns 404
#    for BOTH amd64 and arm64, verified directly, so this is a supply-chain decay
#    problem in the archived repository rather than an architecture limitation.
#    unzip is only used by composer to extract archives, and the zip PHP
#    extension installed below serves the same purpose, so composer still works.
#    libzip-dev comes from bullseye main, which is still served.
#
# The php base image tag, installed extensions, Apache modules, status.conf,
# document root and permissions are unchanged, so both architectures build from
# the same php:7.4-apache tag.

FROM php:7.4-apache
RUN apt-get update && apt-get install -yqq libzip-dev \
    && docker-php-ext-install pdo_mysql opcache zip \
    && rm -rf /var/lib/apt/lists/*
# relax permissions on status
COPY status.conf /etc/apache2/mods-available/status.conf
# Enable Apache mod_rewrite and status
RUN a2enmod rewrite && a2enmod status
WORKDIR /var/www/html
COPY html/ /var/www/html
COPY --from=composer:2.7 /usr/bin/composer /usr/bin/composer
RUN composer install --no-interaction
RUN rm -Rf /var/www/var/*
RUN chown -R www-data /var/www
RUN chmod -R 777 /var/www
