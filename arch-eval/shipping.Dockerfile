# shipping, adapted for the architecture feasibility check.
#
# Two deliberate deviations from sources/robot-shop/shipping/Dockerfile, both
# recorded so the comparison can be interpreted:
#
# 1. The runtime base changes from openjdk:8-jdk to eclipse-temurin:8-jdk.
#    This is forced, not a choice: openjdk:8-jdk no longer exists on Docker Hub,
#    so the original image cannot be rebuilt for amd64 either. Using the same
#    Temurin tag for BOTH architectures is what keeps the comparison honest; if
#    amd64 kept openjdk and arm64 used Temurin, any difference would conflate a
#    JDK change with an architecture change.
#
# 2. The build stage is pinned to BUILDPLATFORM so the JAR is compiled exactly
#    once. Java bytecode is architecture independent, so both runtime images
#    receive a byte-identical artefact and only the runtime JDK differs.
#
# The JVM flags, exposed port, working directory and entrypoint are unchanged.

FROM --platform=$BUILDPLATFORM maven:3.8-eclipse-temurin-8 AS build
WORKDIR /opt/shipping
COPY pom.xml /opt/shipping/
RUN mvn -q dependency:resolve
COPY src /opt/shipping/src/
RUN mvn -q package

FROM eclipse-temurin:8-jdk
EXPOSE 8080
WORKDIR /opt/shipping
ENV CART_ENDPOINT=cart:8080
ENV DB_HOST=mysql
COPY --from=build /opt/shipping/target/shipping-1.0.jar shipping.jar
CMD [ "java", "-Xmn256m", "-Xmx768m", "-jar", "shipping.jar" ]
