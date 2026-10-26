# dispatch, adapted so it can be built reproducibly.
#
# The published Dockerfile cannot be built today, on any architecture. Verified
# identical failure on linux/amd64 and linux/arm64:
#
#   github.com/instana/go-sensor imports
#       slices: package slices is not in GOROOT
#
# Cause: the repository commits only main.go, with no go.mod or go.sum, and the
# build runs "go get" with no arguments. Dependencies therefore always resolved to
# whatever was latest at build time. The current latest go-sensor needs the slices
# package, added to the standard library in Go 1.21, so the pinned Go 1.17
# toolchain cannot build it.
#
# Raising the toolchain was tried and rejected: with golang:1.23 the resolver then
# reported "go-sensor@v1.79.0 requires go >= 1.24.0". Chasing the toolchain simply
# follows a moving target and would leave the build unpinned.
#
# Fix applied instead: keep the originally pinned golang:1.17 and pin the
# dependency to the last go-sensor release published BEFORE the pinned Robot Shop
# commit of 2023-03-06. That is v1.52.0, dated 2023-02-28, which declares "go
# 1.13" and so builds on Go 1.17. This is the closest reproducible approximation
# to the dependency set the published image would have used.
#
# The resolved go.mod and go.sum are copied to the image root so a later build can
# be compared against them, and so the floating resolution is visible.
#
# dispatch is background infrastructure for the five selected repair families; no
# candidate playbook targets it.

FROM golang:1.17
WORKDIR /go/src/app
COPY *.go .
RUN go mod init dispatch \
 && go get github.com/instana/go-sensor@v1.52.0 \
 && go mod tidy
RUN go install
RUN cp go.mod go.sum /
CMD dispatch
