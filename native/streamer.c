/* TECHO5 managed best-effort log sender. No shell, flash spool or inbound socket. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define VERSION "0.1.1"
#define LINE 2048
#define FRAME 2560
#define SLOTS 96
#define SOURCES 16
#define BUDGET 32768
#ifndef SOURCE_UID
#define SOURCE_UID 0
#endif
struct source { const char *path, *tag; int fd; dev_t dev; ino_t ino; off_t pos; size_t used; int cut; char line[LINE]; };
static struct source sources[SOURCES];
static int ns, sock=-1, connecting, kfd=-1, qhead, qcount;
static struct { size_t len, off; char bytes[FRAME]; } queue[SLOTS];
static unsigned long long sent, dropped, truncated, lost, read_errors, kernel_records, file_gaps;
static uint64_t last_seq; static int have_seq;
static volatile sig_atomic_t stopping;
static char boot[40], device[64];
static struct sockaddr_in endpoint;
static double retry_at, connect_at, last_progress;
static unsigned retry_delay=1;
static const char *status_path;

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec/1e9; }
static void stop(int sig) { (void)sig; stopping=1; }
static void disconnect(void) {
    if(sock>=0) close(sock);
    sock=-1; connecting=0;
    /* A partial record cannot be continued on a fresh TCP stream. */
    if(qcount && queue[qhead].off) { dropped++; qhead=(qhead+1)%SLOTS; qcount--; }
    retry_at=now()+retry_delay+(getpid()%100)/100.0;
    if(retry_delay<60) retry_delay=retry_delay>30?60:retry_delay*2;
}
static void emit(const char *tag, int priority, const char *msg, int cut) {
    if(qcount==SLOTS) { dropped++; return; }
    int at=(qhead+qcount)%SLOTS;
    char stamp[32], clean[LINE]; time_t t=time(NULL); struct tm tm;
    gmtime_r(&t,&tm); strftime(stamp,sizeof stamp,"%b %e %H:%M:%S",&tm);
    size_t i=0; for(;msg[i] && i<sizeof clean-1;i++) {
        unsigned char c=(unsigned char)msg[i]; clean[i]=(c<32 || c==127)?' ':c;
    } clean[i]=0;
    int n=snprintf(queue[at].bytes,FRAME,"<%d>%s %s satellite-%s: boot=%s%s %s\n",
        priority,stamp,device,tag,boot,cut?" truncated=1":"",clean);
    if(n<0 || n>=FRAME) { dropped++; return; }
    queue[at].len=(size_t)n; queue[at].off=0; qcount++;
    if(cut) truncated++;
}
static void source_add(const char *path,const char *tag) {
    sources[ns]=(struct source){.path=path,.tag=tag,.fd=-1}; ns++;
}
static int regular_open(const char *path,struct stat *st) {
    int fd=open(path,O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC);
    if(fd<0) return -1;
    if(fstat(fd,st) || !S_ISREG(st->st_mode) || st->st_uid!=SOURCE_UID) { close(fd);return -1; }
    return fd;
}
static void file_read(struct source *s) {
    struct stat st;
    if(lstat(s->path,&st) || !S_ISREG(st.st_mode) || st.st_uid!=SOURCE_UID) {
        if(s->fd>=0) close(s->fd);
        s->fd=-1; s->used=0;s->cut=0;return;
    }
    if(s->fd<0 || st.st_dev!=s->dev || st.st_ino!=s->ino) {
        if(s->fd>=0) {struct stat previous;if(!fstat(s->fd,&previous) && previous.st_size>s->pos)file_gaps++;close(s->fd);}
        int first=s->ino==0;
        s->fd=regular_open(s->path,&st); if(s->fd<0) return;
        s->dev=st.st_dev;s->ino=st.st_ino;s->used=0;s->cut=0;
        /* Bounded initial history; never upload the complete old log. */
        s->pos=(first && st.st_size>8192)?st.st_size-8192:0;
        if(s->pos) s->cut=2; /* discard initial partial line */
    }
    if(st.st_size<s->pos) { file_gaps++;s->pos=0;s->used=0;s->cut=0; }
    char buf[4096]; int budget=BUDGET;
    while(budget>0) {
        ssize_t n=pread(s->fd,buf,sizeof buf,s->pos);
        if(n<=0) { if(n<0 && errno!=EINTR) read_errors++; break; }
        s->pos+=n; budget-=(int)n;
        for(ssize_t i=0;i<n;i++) {
            if(qcount>=SLOTS-8) {s->pos-=n-i;return;}
            if(buf[i]=='\n') {
                if(s->cut!=2) { s->line[s->used]=0;emit(s->tag,134,s->line,s->cut); }
                s->used=0;s->cut=0;
            } else if(s->cut!=2) {
                if(s->used<LINE-1) s->line[s->used++]=buf[i]?buf[i]:' ';
                else s->cut=1;
            }
        }
    }
}
static void kernel_read(void) {
    if(kfd<0) return;
    char buf[8192], msg[LINE];
    for(int i=0;i<64 && qcount<SLOTS-8;i++) {
        ssize_t n=read(kfd,buf,sizeof buf-1);
        if(n<0) { if(errno==EPIPE) { read_errors++;continue; } if(errno!=EAGAIN && errno!=EINTR) read_errors++;break; }
        if(!n) break; buf[n]=0;
        unsigned pri; unsigned long long seq,us; int pos=0;
        if(sscanf(buf,"%u,%llu,%llu,%*[^;];%n",&pri,&seq,&us,&pos)!=3 || pos<=0) {read_errors++;continue;}
        if(have_seq && seq>last_seq+1) lost+=seq-last_seq-1;
        last_seq=seq;have_seq=1;kernel_records++;
        /* Kernel record may have metadata continuation lines; keep one framed message. */
        int len=snprintf(msg,sizeof msg,"seq=%llu mono_us=%llu %s",seq,us,buf+pos);
        emit("kernel",(int)(pri%192),msg,len>=(int)sizeof msg);
    }
}
static void connect_start(void) {
    if(sock>=0 || now()<retry_at) return;
    sock=socket(AF_INET,SOCK_STREAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
    if(sock<0) {disconnect();return;}
    int size=16384;setsockopt(sock,SOL_SOCKET,SO_SNDBUF,&size,sizeof size);
    connect_at=last_progress=now();
    int rc=connect(sock,(struct sockaddr*)&endpoint,sizeof endpoint);
    if(rc==0) {connecting=0;return;}
    if(errno==EINPROGRESS) connecting=1; else disconnect();
}
static void write_queue(void) {
    int budget=BUDGET;
    while(sock>=0 && !connecting && qcount && budget>0) {
        ssize_t n=send(sock,queue[qhead].bytes+queue[qhead].off,queue[qhead].len-queue[qhead].off,MSG_NOSIGNAL);
        if(n<0) { if(errno!=EAGAIN && errno!=EINTR) disconnect();break; }
        if(!n) {disconnect();break;}
        last_progress=now();retry_delay=1;queue[qhead].off+=(size_t)n;budget-=(int)n;
        if(queue[qhead].off==queue[qhead].len) {sent++;qhead=(qhead+1)%SLOTS;qcount--;}
    }
}
static void status_write(void) {
    /* Runtime-only metadata; never record message bodies or credentials. */
    char tmp[256]; if(snprintf(tmp,sizeof tmp,"%s.tmp",status_path)>=(int)sizeof tmp) return;
    int fd=open(tmp,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);
    if(fd<0) return;
    struct stat st;if(fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_uid!=getuid() || st.st_nlink!=1) {close(fd);return;}
    int available=0;for(int i=0;i<ns;i++)if(sources[i].fd>=0)available++;
    struct timespec cpu;clock_gettime(CLOCK_PROCESS_CPUTIME_ID,&cpu);
    unsigned long long cpu_us=(unsigned long long)cpu.tv_sec*1000000+cpu.tv_nsec/1000;
    dprintf(fd,"version=%s\nconnected=%d\nwritten=%llu\ndropped=%llu\ntruncated=%llu\nkernel_lost=%llu\nkernel_records=%llu\nread_errors=%llu\nfiles=%d\nkernel_open=%d\nqueue=%d\nuptime=%llu\ncpu_us=%llu\nfile_gaps=%llu\n",VERSION,sock>=0&&!connecting,sent,dropped,truncated,lost,kernel_records,read_errors,available,kfd>=0,qcount,(unsigned long long)now(),cpu_us,file_gaps);
    close(fd);rename(tmp,status_path);
}
static int valid_device(const char *name) {
    size_t n=strlen(name);
    if(!n || n>=sizeof device) return 0;
    for(size_t i=0;i<n;i++) {
        unsigned char c=(unsigned char)name[i];
        if(!((c>='A' && c<='Z') || (c>='a' && c<='z') ||
             (c>='0' && c<='9') || c=='-' || c=='_' || c=='.')) return 0;
    }
    return 1;
}
int main(int argc,char **argv) {
    if(argc==2 && !strcmp(argv[1],"--version")) {puts(VERSION);return 0;}
    /* Positional validated data: destination, port, configured device token, profile, runtime status. */
    if(argc!=6) return 2;
    char *end;long port=strtol(argv[2],&end,10);

    if(*end || port<1024 || port>65535 || inet_pton(AF_INET,argv[1],&endpoint.sin_addr)!=1 || !valid_device(argv[3])) return 2;
    if(strcmp(argv[4],"dot") && strcmp(argv[4],"screen")) return 2;
    snprintf(device,sizeof device,"%s",argv[3]);status_path=argv[5];
    endpoint.sin_family=AF_INET;endpoint.sin_port=htons((uint16_t)port);
    umask(077);signal(SIGTERM,stop);signal(SIGINT,stop);signal(SIGPIPE,SIG_IGN);
    int bf=open("/proc/sys/kernel/random/boot_id",O_RDONLY|O_CLOEXEC);
    if(bf>=0){ssize_t n=read(bf,boot,sizeof boot-1);if(n>0)boot[strcspn(boot,"\r\n")]=0;close(bf);}
    kfd=open("/dev/kmsg",O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_NOFOLLOW);
    if(kfd>=0) {struct stat st;if(fstat(kfd,&st) || !S_ISCHR(st.st_mode)) {close(kfd);kfd=-1;}}
    if(kfd<0) return 3;
    if(!strcmp(argv[4],"dot")) {
        source_add("/data/techo5-linux/echod.log","application");
        source_add("/data/techo5-linux/wpa.log","wifi");source_add("/data/techo5-linux/udhcpc.log","dhcp");
        source_add("/data/techo5-linux/wmtup.log","chipset");source_add("/data/techo5-linux/first-boot.log","first-boot");
        source_add("/data/techo5-linux/dbus.log","dbus");source_add("/data/techo5-linux/bluetoothd.log","bluetooth");
        source_add("/data/techo5-linux/btbridge.log","btbridge");source_add("/data/techo5-linux/bluealsa.log","bluealsa");
    } else {
        source_add("/data/techo5-linux/techo5.log","application");source_add("/run/boot.log","boot-live");
        source_add("/data/techo5-linux/boot.log","boot-saved");source_add("/data/techo5-linux/firewall.log","firewall");
        source_add("/data/techo5-linux/rescue.log","rescue");source_add("/tmp/udhcpc.log","dhcp");source_add("/tmp/wpa.log","wifi");
        source_add("/var/log/dbus.log","dbus");source_add("/var/log/bluetoothd.log","bluetooth");
        source_add("/var/log/btbridge.log","btbridge");source_add("/var/log/bluealsa.log","bluealsa");
    }
    source_add("/tmp/ntpd.log","ntp");
    double scan_at=0,status_at=0;
    while(!stopping) {
        double t=now();
        if(t>=scan_at){for(int i=0;i<ns;i++)file_read(&sources[i]);scan_at=t+2;}
        connect_start();
        struct pollfd p[2]={{kfd,POLLIN,0},{sock,(short)(POLLIN|((connecting||qcount)?POLLOUT:0)),0}};
        poll(p,2,250);
        if(p[0].revents&(POLLIN|POLLERR))kernel_read();
        if(sock>=0 && (p[1].revents&(POLLERR|POLLHUP|POLLNVAL)))disconnect();
        if(sock>=0 && connecting && (p[1].revents&POLLOUT)) {
            int err=0;socklen_t len=sizeof err;
            if(getsockopt(sock,SOL_SOCKET,SO_ERROR,&err,&len) || err)disconnect();else connecting=0;
        }
        if(sock>=0 && !connecting && (p[1].revents&POLLIN)) {
            char discard[64];ssize_t n=recv(sock,discard,sizeof discard,0);if(n==0 || (n<0 && errno!=EAGAIN && errno!=EINTR))disconnect();
        }
        if(sock>=0 && ((connecting && now()-connect_at>5)||(!connecting && qcount && now()-last_progress>10)))disconnect();
        write_queue();
        if(now()>=status_at){status_write();status_at=now()+10;}
        /* Bound work even when a kernel/log/socket is permanently ready. */
        double wait=t+0.25-now();
        if(wait>0){struct timespec pause={0,(long)(wait*1e9)};nanosleep(&pause,NULL);}
    }
    if(sock>=0)close(sock);if(kfd>=0)close(kfd);
    for(int i=0;i<ns;i++)if(sources[i].fd>=0)close(sources[i].fd);
    status_write();return 0;
}
