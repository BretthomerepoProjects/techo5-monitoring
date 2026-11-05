/* Synthetic file/socket tests; never touches a satellite or a real log. */
#define SOURCE_UID getuid()
#define main streamer_main
#include "streamer.c"
#undef main
#include <assert.h>

static void reset_queue(void) {qhead=qcount=0;sent=dropped=truncated=0;}
static void put(const char *path,const char *text,const char *mode) {
    FILE *f=fopen(path,mode);assert(f);fputs(text,f);fclose(f);
}
int main(void) {
    assert(valid_device("Show8-1") && valid_device("Office_Dot.2"));
    assert(!valid_device("") && !valid_device("bad name") && !valid_device("bad\nforged"));
    char too_long[65];memset(too_long,'x',64);too_long[64]=0;assert(!valid_device(too_long));
    strcpy(device,"Show8-1");strcpy(boot,"synthetic-boot");
    emit("test",134,"hello\nforged\rheader",0);
    assert(qcount==1 && strstr(queue[0].bytes," Show8-1 satellite-test:") && strstr(queue[0].bytes,"hello forged header\n"));
    for(int i=0;i<SLOTS+10;i++)emit("test",134,"bounded",0);
    assert(qcount==SLOTS && dropped==11);
    reset_queue();
    char dir[]="/tmp/t5sm-native-XXXXXX";assert(mkdtemp(dir));
    char path[256],old[256],link[256],status[256];
    snprintf(path,sizeof path,"%s/log",dir);snprintf(old,sizeof old,"%s/old",dir);
    snprintf(link,sizeof link,"%s/link",dir);snprintf(status,sizeof status,"%s/status",dir);
    struct source s={.path=path,.tag="test",.fd=-1};
    put(path,"first\npart","w");file_read(&s);assert(qcount==1 && s.used==4);
    put(path,"ial\n","a");file_read(&s);assert(qcount==2 && strstr(queue[1].bytes,"partial\n"));
    put(path,"new\n","w");file_read(&s);assert(qcount==3 && strstr(queue[2].bytes,"new\n"));
    assert(!rename(path,old));put(path,"rotated\n","w");file_read(&s);assert(qcount==4 && strstr(queue[3].bytes,"rotated\n"));
    put(path,"","w");file_read(&s);
    FILE *f=fopen(path,"a");assert(f);for(int i=0;i<100000;i++)fputc('x',f);fputs("\nvalid\n",f);fclose(f);
    for(int i=0;i<4;i++)file_read(&s);
    assert(qcount==6 && truncated==1 && strstr(queue[4].bytes,"truncated=1"));
    assert(strstr(queue[5].bytes,"valid\n"));
    reset_queue();
    for(int i=0;i<SLOTS-8;i++)emit("test",134,"full",0);
    put(path,"deferred\n","w");struct source pending={.path=path,.tag="test",.fd=-1};
    file_read(&pending);assert(pending.pos==0 && dropped==0);
    reset_queue();file_read(&pending);assert(qcount==1 && strstr(queue[0].bytes,"deferred\n"));close(pending.fd);
    assert(!symlink(path,link));struct source untrusted={.path=link,.tag="bad",.fd=-1};file_read(&untrusted);assert(untrusted.fd==-1);
    reset_queue();emit("test",134,"wire record",0);
    int pair[2];assert(!socketpair(AF_UNIX,SOCK_STREAM|SOCK_NONBLOCK,0,pair));sock=pair[0];connecting=0;
    write_queue();char wire[FRAME];ssize_t n=read(pair[1],wire,sizeof wire-1);assert(n>0);wire[n]=0;
    assert(sent==1 && qcount==0 && strstr(wire,"satellite-test:"));close(pair[1]);disconnect();
    reset_queue();emit("test",134,"partial",0);queue[0].off=10;disconnect();assert(dropped==1 && qcount==0);
    reset_queue();assert(!socketpair(AF_UNIX,SOCK_STREAM|SOCK_NONBLOCK,0,pair));sock=pair[0];connecting=0;close(pair[1]);
    emit("test",134,"outage",0);write_queue();assert(sock==-1 && qcount==1 && sent==0);
    retry_at=now()+10;connect_start();assert(sock==-1);
    reset_queue();
    int pipefd[2];assert(!pipe2(pipefd,O_NONBLOCK));kfd=pipefd[0];
    char rec[]="3,100,500,-;synthetic kernel\n";assert(write(pipefd[1],rec,strlen(rec))>0);kernel_read();
    assert(kernel_records==1 && qcount==1 && strstr(queue[0].bytes,"seq=100 mono_us=500"));
    char rec2[]="4,104,600,-;gap\n";assert(write(pipefd[1],rec2,strlen(rec2))>0);kernel_read();assert(lost==3);
    status_path=status;status_write();f=fopen(status,"r");assert(f);char content[2048];size_t count=fread(content,1,sizeof content-1,f);content[count]=0;fclose(f);
    assert(strstr(content,"kernel_records=2\n") && !strstr(content,"synthetic kernel"));
    close(pipefd[0]);close(pipefd[1]);close(s.fd);unlink(path);unlink(old);unlink(link);unlink(status);rmdir(dir);
    puts("native buffer, injection, partial-line, truncation, rotation, symlink, TCP framing, reconnect, kernel sequence and scalar-status tests passed");
    return 0;
}
