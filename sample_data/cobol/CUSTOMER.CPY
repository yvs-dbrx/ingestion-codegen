      *================================================================*
      * COPYBOOK : CUSTOMER.CPY                                        *
      * PURPOSE  : Customer master with contact and account balances   *
      * RECLEN   : 248 bytes, fixed length, EBCDIC (cp037)             *
      *                                                                *
      * FIELD MAP (offset is 1-based):                                 *
      *    1-8    CUST-ID              9(8)         zoned              *
      *    9-23   CUST-FIRST-NAME      X(15)        text               *
      *   24-24   CUST-MIDDLE-INIT     X            text               *
      *   25-44   CUST-LAST-NAME       X(20)        text               *
      *   45-74   CUST-STREET-LINE1    X(30)        text               *
      *   75-104  CUST-STREET-LINE2    X(30)        text               *
      *  105-124  CUST-CITY            X(20)        text               *
      *  125-126  CUST-STATE-CODE      XX           text               *
      *  127-136  CUST-ZIP-CODE        X(10)        text               *
      *  137-139  CUST-COUNTRY-CODE    X(3)         text               *
      *  140-154  CUST-PHONE           X(15)        text               *
      *  155-194  CUST-EMAIL           X(40)        text               *
      *  195-195  CUST-TYPE            X            text               *
      *  196-196  CUST-STATUS          X            text               *
      *  197-204  CUST-DATE-ADDED      9(8)         zoned              *
      *  205-212  CUST-DATE-MODIFIED   9(8)         zoned              *
      *  213-217  CUST-CREDIT-LIMIT    S9(7)V99     COMP-3             *
      *  218-223  CUST-BALANCE         S9(9)V99     COMP-3             *
      *  224-232  CUST-YTD-RETURNS     S9(7)V99     zoned              *
      *  233-236  CUST-LOYALTY-POINTS  S9(9)        COMP               *
      *  237-238  CUST-ORDER-COUNT     9(4)         COMP               *
      *  239-248  FILLER               X(10)        text               *
      *                                                                *
      * CUST-TYPE  : R=Retail W=Wholesale P=Premium C=Corporate        *
      * CUST-STATUS: A=Active I=Inactive S=Suspended                   *
      * Dates are YYYYMMDD; 00000000 means 'never modified'.           *
      *================================================================*
       01  CUSTOMER-RECORD.
           05  CUST-ID               PIC 9(8).
           05  CUST-NAME.
               10  CUST-FIRST-NAME       PIC X(15).
               10  CUST-MIDDLE-INIT      PIC X.
               10  CUST-LAST-NAME        PIC X(20).
           05  CUST-ADDRESS.
               10  CUST-STREET-LINE1     PIC X(30).
               10  CUST-STREET-LINE2     PIC X(30).
               10  CUST-CITY             PIC X(20).
               10  CUST-STATE-CODE       PIC XX.
               10  CUST-ZIP-CODE         PIC X(10).
               10  CUST-COUNTRY-CODE     PIC X(3).
           05  CUST-CONTACT.
               10  CUST-PHONE            PIC X(15).
               10  CUST-EMAIL            PIC X(40).
           05  CUST-TYPE             PIC X.
           05  CUST-STATUS           PIC X.
           05  CUST-DATE-ADDED       PIC 9(8).
           05  CUST-DATE-MODIFIED    PIC 9(8).
           05  CUST-FINANCIAL.
               10  CUST-CREDIT-LIMIT     PIC S9(7)V99 COMP-3.
               10  CUST-BALANCE          PIC S9(9)V99 COMP-3.
               10  CUST-YTD-RETURNS      PIC S9(7)V99.
               10  CUST-LOYALTY-POINTS   PIC S9(9) COMP.
               10  CUST-ORDER-COUNT      PIC 9(4) COMP.
           05  FILLER                PIC X(10).
